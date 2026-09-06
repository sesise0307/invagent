import os
from dataclasses import dataclass, field
from pathlib import Path


DEFAULT_CHANNELS: tuple[str, ...] = (
    "DOC_POOL",
    "선진짱 주식공부방",
    "report_figure_by_offset",
    "YoungTiger_stock",
    "quick_report",
)


def _parse_default_channels(raw_value: str) -> tuple[str, ...]:
    """Parse a comma-separated channel list from the environment."""
    channels = tuple(channel.strip() for channel in raw_value.split(",") if channel.strip())
    return channels or DEFAULT_CHANNELS


@dataclass
class Config:
    """Telegram API settings and local output conventions."""

    api_id: int
    api_hash: str
    session_path: Path
    output_dir: Path
    default_channels: tuple[str, ...] = field(default_factory=lambda: DEFAULT_CHANNELS)

    def digest_raw_dir(self) -> Path:
        """Return the directory holding raw daily exports."""
        return self.output_dir / "daily-digest/raw"

    def digest_media_dir(self, date_str: str) -> Path:
        """Return the directory holding downloaded media for one export date.

        Sibling of the raw export directory so the daily-digest cleanup
        step can retire both on the same schedule.
        """
        return self.output_dir / "daily-digest/media" / date_str

    @classmethod
    def from_env(cls) -> "Config":
        """Load configuration from environment variables."""
        api_id_str = os.environ.get("TELEGRAM_API_ID", "").strip()
        api_hash = os.environ.get("TELEGRAM_API_HASH", "").strip()

        if not api_id_str:
            raise ValueError("TELEGRAM_API_ID 환경변수가 설정되지 않았습니다.")
        if not api_hash:
            raise ValueError("TELEGRAM_API_HASH 환경변수가 설정되지 않았습니다.")

        try:
            api_id = int(api_id_str)
        except ValueError:
            raise ValueError(f"TELEGRAM_API_ID는 정수여야 합니다: {api_id_str}")

        session_path = Path(
            os.environ.get("TELEGRAM_SESSION_PATH", str(Path.home() / ".telegram_session"))
        ).expanduser()
        output_dir = Path(os.environ.get("INVAGENT_OUTPUT_DIR", "output")).expanduser()
        default_channels = _parse_default_channels(
            os.environ.get("INVAGENT_DEFAULT_CHANNELS", ",".join(DEFAULT_CHANNELS))
        )

        return cls(
            api_id=api_id,
            api_hash=api_hash,
            session_path=session_path,
            output_dir=output_dir,
            default_channels=default_channels,
        )
