from dataclasses import dataclass
from typing import Callable

from pydantic import BaseModel, ConfigDict

from app.core.errors import AppError
from app.domain.schemas import Execution


class CreateTasksArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


@dataclass(frozen=True)
class ToolContext:
    execution_id: str
    run_id: str


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, tuple[type[BaseModel], Callable]] = {}

    def register(self, name: str, arguments: type[BaseModel], handler: Callable):
        if name in self._tools:
            raise ValueError("工具重复注册")
        self._tools[name] = (arguments, handler)

    def catalog(self):
        return [{"name": name, "arguments_schema": arguments.model_json_schema()}
                for name, (arguments, _) in self._tools.items()]

    def invoke(self, name: str, arguments: dict, context: ToolContext) -> Execution:
        if name not in self._tools:
            raise AppError("tool_not_allowed", "请求的工具不在白名单中", 422)
        schema, handler = self._tools[name]
        try:
            validated = schema.model_validate(arguments)
        except ValueError as exc:
            raise AppError("invalid_tool_arguments", "工具参数不符合约定", 422) from exc
        return handler(context, validated)
