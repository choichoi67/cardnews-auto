import getpass
import re
from pathlib import Path

from openai import AuthenticationError, OpenAI, PermissionDeniedError, RateLimitError

from .telegram_bot import _update_env


def _clean_api_key(value: str) -> str:
    cleaned = value.strip()
    if cleaned.upper().startswith("OPENAI_API_KEY="):
        cleaned = cleaned.split("=", 1)[1]
    cleaned = cleaned.strip().strip('"\'')
    cleaned = re.sub(r"[\s\u200b\u200c\u200d\ufeff]+", "", cleaned)
    if cleaned.lower().startswith("bearer"):
        cleaned = cleaned[6:].lstrip(":")
    return cleaned


def _select_model(available: set[str], configured: str) -> str:
    if configured in available:
        return configured
    model = next((model for model in (
        "gpt-6.1-sol",
        "gpt-6-sol",
        "gpt-5.6-terra",
        "gpt-5.6-luna",
        "gpt-5.4-mini",
        "gpt-5-mini",
        "gpt-4.1-mini",
        "gpt-4o-mini",
    ) if model in available), None)
    if model:
        return model
    raise RuntimeError(
        "이 키에서 사용할 수 있는 호환 모델을 찾지 못했습니다. "
        "OpenAI 프로젝트의 모델 권한을 확인하세요."
    )


def setup_interactive(env_path: Path, configured_model: str) -> str:
    print("OpenAI Platform에서 발급받은 API 키를 입력하세요.")
    api_key = _clean_api_key(getpass.getpass("OpenAI API key (화면에 표시되지 않음): "))
    if not api_key.startswith("sk-"):
        raise RuntimeError("Secret key 전체가 아닙니다. 새 Secret key를 생성해 sk-로 시작하는 값을 복사하세요.")
    if "*" in api_key or "..." in api_key:
        raise RuntimeError("대시보드에 가려져 표시된 키는 사용할 수 없습니다. 새 Secret key를 생성하세요.")

    client = OpenAI(api_key=api_key)
    try:
        available = {model.id for model in client.models.list().data}
    except AuthenticationError:
        raise RuntimeError("API 키가 잘못되었거나 폐기되었습니다. Secret key 전체를 다시 복사하세요.") from None
    except PermissionDeniedError:
        raise RuntimeError("API 프로젝트에서 모델 조회 권한이 없습니다. 키의 프로젝트 권한을 확인하세요.") from None
    except RateLimitError:
        raise RuntimeError("API 사용 한도 또는 결제 크레딧을 확인하세요.") from None
    except Exception as exc:
        raise RuntimeError(f"OpenAI 연결 중 오류가 발생했습니다: {type(exc).__name__}: {exc}") from None

    model = _select_model(available, configured_model)
    _update_env(env_path, {"OPENAI_API_KEY": api_key, "OPENAI_MODEL": model})
    return model
