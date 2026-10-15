"""Domain errors for agent tool authorization."""


class AgentAccessDeniedError(Exception):
    """The authenticated actor cannot execute this tool."""
