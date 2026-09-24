"""Errors that carry a fixed code instead of free text.

Every message here is written by CHONK. Library exception text is never
copied into these messages: it can quote document objects, and in privacy mode
anything a message contains may reach the AI model.
"""

from __future__ import annotations


class ChonkError(Exception):
    """An expected failure. ``code`` is part of CHONK's fixed vocabulary."""

    code = "error:internal"
    message = "CHONK hit an internal error."

    def __init__(self, message: str | None = None):
        super().__init__(message or self.message)


class NotPdfError(ChonkError):
    code = "error:not_pdf"
    message = "The file is not a PDF."


class EncryptedPdfError(ChonkError):
    code = "error:encrypted"
    message = "The PDF is password-protected. Unlock it before compressing."


class UnreadablePdfError(ChonkError):
    code = "error:unreadable"
    message = "The PDF could not be read."


class EmptyPdfError(ChonkError):
    code = "error:empty"
    message = "The PDF has no pages."


class TooLargeError(ChonkError):
    code = "error:too_large"
    message = "The PDF is larger than CHONK's input limit."


class OutputInvalidError(ChonkError):
    code = "error:internal"
    message = "CHONK produced an output that failed validation; nothing was saved."
