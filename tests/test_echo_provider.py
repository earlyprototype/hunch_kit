"""Tests for the echo provider."""

from hunch_kit.providers.echo import EchoProvider


class TestEchoProvider:
    def test_echoes_input(self):
        provider = EchoProvider()
        result = provider.run("hello world")
        assert result.output == "hello world"
        assert result.status == "success"

    def test_prefix_config(self):
        provider = EchoProvider()
        result = provider.run("test", config={"prefix": "ECHO: "})
        assert result.output == "ECHO: test"

    def test_records_metadata(self):
        provider = EchoProvider()
        result = provider.run("data")
        assert result.metadata["provider"] == "echo"
        assert result.metadata["input_length"] == 4

    def test_delay_config(self):
        provider = EchoProvider()
        result = provider.run("data", config={"delay": 0.05})
        assert result.duration_seconds >= 0.04

    def test_name_attribute(self):
        provider = EchoProvider()
        assert provider.name == "echo"
