"""Abstract base class for all report delivery channels."""

from abc import ABC, abstractmethod

from src.models import Report


class BaseDelivery(ABC):
    """Abstract base for any delivery channel (WhatsApp, Email, etc.).

    Concrete channels must:
      - Set `channel_name` (used in logs).
      - Implement `send(report)` returning True on success, False on failure.
    """

    channel_name: str = "unknown"

    @abstractmethod
    async def send(self, report: Report) -> bool:
        """Deliver the daily report. Return True on success, False otherwise."""
        raise NotImplementedError
