"""Runtime exception taxonomy."""


class DataUnavailable(RuntimeError):
    """Raised when market data cannot support a trading decision."""


class BrokerUnavailable(RuntimeError):
    """Raised when broker truth cannot be read or written."""


class BrokerAmbiguous(RuntimeError):
    """Raised when broker order or exposure state cannot be proven."""


class StrategyFailed(RuntimeError):
    """Raised when a strategy evaluation fails."""


class RiskBlocked(RuntimeError):
    """Raised when risk policy blocks a target."""


class ExecutionFailed(RuntimeError):
    """Raised when execution fails with broker state still known."""


class CleanupFailed(RuntimeError):
    """Raised when required flattening cannot be proven."""


class ConfigInvalid(ValueError):
    """Raised when runtime configuration is malformed."""
