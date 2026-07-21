"""환경 설정 로딩.

환경변수와 프로젝트 루트의 .env 파일에서 설정을 읽는다.
외부 라이브러리 없이 동작하는 단순 로더를 사용한다.

인증키 값은 로그, 예외 메시지, repr 어디에도 노출하지 않는다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ENV_FILE_NAME = ".env"
API_KEY_ENV_VAR = "KSTARTUP_API_KEY"

MISSING_KEY_MESSAGE = (
    "KSTARTUP_API_KEY가 설정되지 않았습니다.\n"
    ".env.example을 .env로 복사한 뒤 일반 인증키(Decoding)를 입력하세요."
)


class ConfigError(Exception):
    """설정 오류. 메시지에 인증키 값을 포함하지 않는다."""


def parse_env_file(path: Path) -> dict[str, str]:
    """단순 .env 파서. `KEY=VALUE` 형식과 줄 단위 `#` 주석만 지원한다.

    값 뒤에 붙는 인라인 주석(`KEY=값 # 설명`)은 지원하지 않으며 값의 일부로
    취급된다. 파일은 UTF-8(BOM 허용)이어야 한다.
    """
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    # UnicodeDecodeError는 .object에 파일 원문 바이트(인증키 포함 가능)를 담으므로
    # 예외 체인(__context__)에 남지 않도록 except 블록 밖에서 ConfigError를 raise한다.
    content: str | None = None
    try:
        content = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        pass
    if content is None:
        # PowerShell 5.1의 `>` 리다이렉션은 UTF-16으로 저장하는 경우가 있다.
        raise ConfigError(
            f"{path.name} 파일을 UTF-8로 읽을 수 없습니다.\n"
            "파일을 UTF-8 인코딩으로 다시 저장하세요 (메모장: 다른 이름으로 저장 → 인코딩 UTF-8)."
        )
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


@dataclass(frozen=True)
class Settings:
    api_key: str

    def __repr__(self) -> str:
        return "Settings(api_key='***')"


def load_settings(project_root: Path | None = None) -> Settings:
    """환경변수를 우선 사용하고, 없으면 프로젝트 루트의 .env를 읽는다.

    인증키가 어디에도 없으면 안내 메시지와 함께 ConfigError를 던진다.
    """
    api_key = os.environ.get(API_KEY_ENV_VAR, "").strip()
    if not api_key:
        root = project_root if project_root is not None else Path.cwd()
        env_values = parse_env_file(root / ENV_FILE_NAME)
        api_key = env_values.get(API_KEY_ENV_VAR, "").strip()
    if not api_key:
        raise ConfigError(MISSING_KEY_MESSAGE)
    return Settings(api_key=api_key)
