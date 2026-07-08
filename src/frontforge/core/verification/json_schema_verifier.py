"""Generic verifier applied to every stage: does the raw output actually
satisfy its Pydantic model?"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ValidationError

from frontforge.core.session import RunSession
from frontforge.shared.types import VerificationIssue


class JsonSchemaVerifier:
    name = "json_schema"

    def __init__(self, model: type[BaseModel]):
        self.model = model

    async def verify(
        self, *, stage_id: str, output: dict[str, Any], session: RunSession
    ) -> list[VerificationIssue]:
        try:
            self.model.model_validate(output)
        except ValidationError as exc:
            return [
                VerificationIssue(
                    verifier=self.name,
                    message=f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}",
                )
                for err in exc.errors()
            ]
        return []
