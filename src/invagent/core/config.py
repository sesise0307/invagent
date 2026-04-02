import os
from pathlib import Path
from dataclasses import dataclass


@dataclass
class Config:
    """Telegram API 설정 및 기타 설정"""
    api_id: int
    api_hash: str
    session_path: Path
    output_dir: Path

    @classmethod
    def from_env(cls) -> "Config":
        """환경변수에서 설정 로드"""
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

        session_path = Path.home() / ".telegram_session"
        output_dir = Path("outputs")

        return cls(
            api_id=api_id,
            api_hash=api_hash,
            session_path=session_path,
            output_dir=output_dir,
        )
