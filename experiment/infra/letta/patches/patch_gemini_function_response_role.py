"""Apply the minimal Gemini 3.x compatibility patch to Letta 0.16.8.

Google's generateContent contract represents a functionResponse as a user turn.
Letta 0.16.8 emits the removed legacy role "function" in exactly two branches of
Message.to_google_dict(). Refuse to build if the pinned upstream file changes.
"""

from pathlib import Path


TARGET = Path("/app/letta/schemas/message.py")
OLD = '"role": "function",'
NEW = '"role": "user",'
EXPECTED_OCCURRENCES = 2


def main() -> None:
    source = TARGET.read_text(encoding="utf-8")
    found = source.count(OLD)
    if found != EXPECTED_OCCURRENCES:
        raise RuntimeError(
            f"refusing compatibility patch: expected {EXPECTED_OCCURRENCES} "
            f"legacy roles in {TARGET}, found {found}"
        )
    TARGET.write_text(source.replace(OLD, NEW), encoding="utf-8")

    patched = TARGET.read_text(encoding="utf-8")
    if OLD in patched or patched.count(NEW) < EXPECTED_OCCURRENCES:
        raise RuntimeError("post-patch verification failed")


if __name__ == "__main__":
    main()
