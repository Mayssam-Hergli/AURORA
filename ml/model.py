"""Temporal graph forecaster (docs/CONTEXT.md Section 5.5).

    input   30 s window of the 33 per-satellite channels + ISL status at the window end
    prep    normalisation, first differences, physical residual dPhi_ij and level residual l_ij on
            every engineering edge (engineering model g_ij fitted on clean episodes)
    memory  GRU over the window for every node (per-type input projection)
    graph   relation-aware message passing (R-GCN-style relation weights + GAT-style attention)
            over E_eng, E_SPARTA, aggregation and E_ISL(t) edges; ISL edges exist only if enabled
    heads   P(F_s < t + D) for D in {10, 30, 60, 120} s (discrete hazard, monotone in D),
            next affected subsystem (softmax over the 6 subsystems), P(malicious | anomaly)

Everything after the raw window is inside the module, so the same code runs in training, in the
online forecaster, in the exported TorchScript model and in the PGD robustness test.
"""

from __future__ import annotations

import math

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

from ml import graph as G

HORIZONS = (10, 30, 60, 120)


class Prep(nn.Module):
    """Raw window [B, W, S, 33] -> node features [B, W, N, 2*F_RAW], residuals [B, W, E_eng, 2]."""

    def __init__(self, und, g: G.Graph, use_residual: bool = True):
        super().__init__()
        self.S = g.n_sats
        self.use_residual = use_residual and len(g.eng_src) > 0
        self.register_buffer("mu", torch.tensor(und.mu, dtype=torch.float32))
        self.register_buffer("sd", torch.tensor(und.sd, dtype=torch.float32))
        self.register_buffer("dsd", torch.tensor(und.dsd, dtype=torch.float32))
        self.pairs = [p for p in G.E_ENG]
        for k, (a, b) in enumerate(self.pairs):
            r = und.ridge[(a, b)]
            for name in ("A", "B", "rsd", "lsd"):
                self.register_buffer(f"r{k}_{name}", torch.tensor(r[name], dtype=torch.float32))
        # node gather: node feature slot j of node type t <- flat channel index (or padding)
        idx = np.full((G.NPS, G.F_RAW), -1)
        for t, name in enumerate(G.NODE_TYPES[:6]):
            a, b = G.SLICE[name]
            idx[t, : b - a] = np.arange(a, b)
        self.register_buffer("gather_idx", torch.tensor(np.where(idx < 0, 0, idx), dtype=torch.long))
        self.register_buffer("gather_mask", torch.tensor((idx >= 0).astype(np.float32)))
        # engineering edges of this graph variant, in order (sat-major, then E_ENG order)
        n_pairs = len(self.pairs)
        self.n_eng = len(g.eng_src)
        self.eng_pair_of = [k % n_pairs for k in range(self.n_eng)]

    def forward(self, raw):
        B, W, S, C = raw.shape
        z = (raw - self.mu) / self.sd
        dz = torch.cat([torch.zeros_like(raw[:, :1]), raw[:, 1:] - raw[:, :-1]], 1) / self.dsd
        gi = self.gather_idx  # [7, F_RAW]
        zn = z[..., gi] * self.gather_mask  # [B, W, S, 7, F_RAW]
        dn = dz[..., gi] * self.gather_mask
        x = torch.cat([zn, dn], -1).reshape(B, W, S * G.NPS, 2 * G.F_RAW)
        if not self.use_residual:
            return x, torch.zeros(B, W, max(self.n_eng, 1), 2, device=raw.device)
        res = []
        for k, (a, b) in enumerate(self.pairs):
            (a0, a1), (b0, b1) = G.SLICE[a], G.SLICE[b]
            za, zb = z[..., a0:a1], z[..., b0:b1]  # [B, W, S, d]
            one = torch.ones_like(zb[..., :1])
            prev = torch.cat([za[:, :-1], zb[:, :-1], one[:, :-1]], -1)
            pred = prev @ getattr(self, f"r{k}_A")
            ch = (((zb[:, 1:] - zb[:, :-1] - pred) / getattr(self, f"r{k}_rsd")) ** 2).mean(-1)
            ch = torch.cat([torch.zeros_like(ch[:, :1]), ch], 1)
            lv = (
                ((zb - torch.cat([za, one], -1) @ getattr(self, f"r{k}_B")) / getattr(self, f"r{k}_lsd")) ** 2
            ).mean(-1)
            res.append(
                torch.stack([torch.log1p(torch.sqrt(ch + 1e-8)), torch.log1p(torch.sqrt(lv + 1e-8))], -1)
            )
        res = torch.stack(res, 3)  # [B, W, S, n_pairs, 2]
        return x, res.reshape(B, W, S * len(self.pairs), 2)


class RelLayer(nn.Module):
    def __init__(self, hid, edim, n_rel=4):
        super().__init__()
        self.W_rel = nn.Parameter(torch.randn(n_rel, hid, hid) / math.sqrt(hid))
        self.msg = nn.Sequential(nn.Linear(2 * hid + edim, hid), nn.ReLU(), nn.Linear(hid, hid))
        self.att = nn.Linear(2 * hid + edim, 1)
        self.norm = nn.LayerNorm(hid)

    def forward(self, h, src, dst, rel, efeat, mask):
        B, N, H = h.shape
        hs, hd = h[:, src], h[:, dst]
        m_in = torch.cat([hs, hd, efeat], -1)
        m = self.msg(m_in) + torch.einsum("beh,ehk->bek", hs, self.W_rel[rel])
        a = 5 * torch.tanh(self.att(m_in).squeeze(-1) / 5)
        w = torch.exp(a) * mask
        denom = torch.zeros(B, N, device=h.device).index_add_(1, dst, w) + 1e-6
        alpha = w / denom[:, dst]
        agg = torch.zeros(B, N, H, device=h.device).index_add_(1, dst, alpha.unsqueeze(-1) * m)
        return self.norm(h + F.relu(agg)), alpha


class Forecaster(nn.Module):
    def __init__(
        self, und, g: G.Graph, hid: int = 48, rounds: int = 3, edge_hid: int = 16, use_residual: bool = True
    ):
        super().__init__()
        self.S, self.N = g.n_sats, g.n_nodes
        self.telemetry_only = False
        self.register_buffer(
            "command_indices",
            torch.tensor(
                [G.FLAT.index(n) for n in G.FLAT if n.startswith(("tc_", "d_cmd_"))], dtype=torch.long
            ),
        )
        self.prep = Prep(und, g, use_residual)
        self.register_buffer("src", torch.tensor(g.src, dtype=torch.long))
        self.register_buffer("dst", torch.tensor(g.dst, dtype=torch.long))
        self.register_buffer("rel", torch.tensor(g.rel, dtype=torch.long))
        coup = np.zeros((g.n_edges, 2), np.float32)
        tof = G.node_type_of(g.n_sats)
        for e in range(g.n_edges):
            key = f"{G.NODE_TYPES[tof[g.src[e]]]}->{G.NODE_TYPES[tof[g.dst[e]]]}"
            if key in und.couplings and g.rel[e] in (0, 1):
                w, tau = und.couplings[key]
                coup[e] = (w, tau / 15.0)
        self.register_buffer("e_static", torch.tensor(np.concatenate([g.flags, coup], 1)))
        self.register_buffer("isl_pair", torch.tensor(g.isl_pair, dtype=torch.long))
        self.register_buffer("eng_pos", torch.tensor(g.eng_pos, dtype=torch.long))
        self.register_buffer("type_of", torch.tensor(tof, dtype=torch.long))
        self.in_proj = nn.Parameter(torch.randn(G.NPS, 2 * G.F_RAW, hid) / math.sqrt(2 * G.F_RAW))
        self.type_emb = nn.Parameter(torch.zeros(G.NPS, hid))
        self.gru = nn.GRU(hid, hid, batch_first=True)
        self.edge_gru = nn.GRU(2, edge_hid, batch_first=True)
        edim = self.e_static.shape[1] + edge_hid
        self.layers = nn.ModuleList([RelLayer(hid, edim) for _ in range(rounds)])
        self.head_hazard = nn.Sequential(nn.Linear(3 * hid, hid), nn.ReLU(), nn.Linear(hid, len(HORIZONS)))
        self.head_mal = nn.Sequential(nn.Linear(3 * hid, hid), nn.ReLU(), nn.Linear(hid, 1))
        self.head_next = nn.Linear(hid, 1)

    def forward(self, raw, links, return_att: bool = False):
        """raw [B, W, S, 33] (physical units), links [B, n_pairs] enabled ISLs at the window end."""
        B, W = raw.shape[:2]
        if self.telemetry_only:
            raw = raw.clone()
            raw[..., self.command_indices] = 0.0
        x, res = self.prep(raw)
        xe = torch.einsum("bwnf,nfh->bwnh", x, self.in_proj[self.type_of]) + self.type_emb[self.type_of]
        _, hN = self.gru(torch.relu(xe).permute(0, 2, 1, 3).reshape(B * self.N, W, -1))
        h = hN[-1].reshape(B, self.N, -1)
        n_res = res.shape[2]
        _, eN = self.edge_gru(res.permute(0, 2, 1, 3).reshape(B * n_res, W, 2))
        eh = eN[-1].reshape(B, n_res, -1)
        pos = self.eng_pos.clamp(min=0)
        edge_dyn = eh[:, pos] * (self.eng_pos >= 0).float()[None, :, None]
        efeat = torch.cat([self.e_static.expand(B, -1, -1), edge_dyn], -1)
        mask = torch.ones(B, len(self.src), device=raw.device)
        is_isl = self.isl_pair >= 0
        mask[:, is_isl] = links[:, self.isl_pair[is_isl]].float()
        atts = []
        for layer in self.layers:
            h, alpha = layer(h, self.src, self.dst, self.rel, efeat, mask)
            atts.append(alpha)
        hs = h.reshape(B, self.S, G.NPS, -1)
        pooled = torch.cat([hs[:, :, -1], hs[:, :, :6].mean(2), hs[:, :, :6].amax(2)], -1)
        bins = self.head_hazard(pooled)  # [B, S, 4] hazard logits
        surv = torch.cumprod(1 - torch.sigmoid(bins), -1)
        out = {
            "bin_logits": bins,
            "p_fail": 1 - surv,
            "mal_logit": self.head_mal(pooled).squeeze(-1),
            "next_logits": self.head_next(hs[:, :, :6]).squeeze(-1),
        }
        if return_att:
            out["attention"] = atts[-1] if atts else mask * 0
        return out


def p_fail_from_bins(bins: torch.Tensor, temperature: float = 1.0) -> torch.Tensor:
    return 1 - torch.cumprod(1 - torch.sigmoid(bins / temperature), -1)
