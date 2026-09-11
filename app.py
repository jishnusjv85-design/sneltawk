"""Default Vercel Python entrypoint for the webhook service."""

from api.tawk import handler


__all__ = ["handler"]
