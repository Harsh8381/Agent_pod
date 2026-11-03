"""Errors raised by the HEDIS engine; the API maps each class to one HTTP status."""


class HedisError(ValueError):
    """A workflow rule refused the request (400)."""


class NotFound(HedisError):
    """The object does not exist (404)."""


class SegregationOfDuties(HedisError):
    """The same person (or an agent) tried to perform two duties that must be separated (409)."""


class Conflict(HedisError):
    """The object is in a state that does not allow this transition (409)."""
