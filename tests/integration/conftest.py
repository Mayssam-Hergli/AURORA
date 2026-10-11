def pytest_configure(config):
    config.addinivalue_line(
        "markers", "slow: end-to-end runs through the real broker (deselect with -m 'not slow')"
    )
