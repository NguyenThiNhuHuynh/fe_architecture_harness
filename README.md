# FrontForge

An AI UI-Architecture Harness: a DAG-orchestrated pipeline of small,
independent agents that turns a raw, messy product requirement into a full
frontend — requirement → business analysis → information architecture →
frontend architecture → design system → page/component planning → codegen →
preview → quality review.

## Design

- **Core only orchestrates** (`frontforge/core/orchestrator.py`): stage
  order, retries, dirty propagation. It has no idea what "codegen" or
  "requirement" mean.
- **Agents are thin plugins** (`frontforge/agents/*/agent.py`): each is a
  ~5-line class naming its stage id and its Pydantic output model. All
  reasoning happens in the LLM, not in Python `if/else`.
- **Providers are swappable** (`frontforge/providers/*`): v1 ships
  `ClaudeCliProvider`, which shells out to the `claude` CLI (`claude -p ...
  --output-format json --json-schema ...`) instead of an API key, so it
  reuses whatever auth Claude Code already has. `OpenAIProvider` /
  `GeminiProvider` are stubs behind the same interface.
- **Verification decides pass/fail**, not the agent
  (`frontforge/core/verification/`). Every stage is checked against its
  JSON Schema; `codegen` additionally runs `tsc`, `eslint` and `npm run
  build` against the generated project.
- **State lives in one place** (`frontforge/core/state_store.py`): a
  `.harness/` directory per project with `state.json` (status + input hash
  per stage) and `outputs/<stage>.json`. Agents never write files directly —
  only `FilesystemTool` writes the generated project, and only after
  verification passes.
- **Prompts are files, not string literals**
  (`frontforge/prompts/<stage>/{system.md,user.md.j2,examples.md}`),
  assembled by `PromptBuilder`.

## Usage

```bash
pip install -e ".[dev]"

# 1. Seed a project workspace from a raw requirement
frontforge init ./my-project --from-file examples/recruitment-platform.brief.yaml

# 2. Run the full pipeline (requires `claude` CLI already authenticated)
frontforge run ./my-project

# 3. Inspect progress
frontforge status ./my-project
frontforge stage show requirement --project ./my-project

# 4. After editing an upstream artifact by hand, re-run just what's affected
frontforge reset ./my-project --stage requirement
frontforge run ./my-project
```

Generated project files land in `./my-project/generated/`.

## Tests

```bash
pytest tests/unit                 # fast, no network, uses a scripted Provider
FRONTFORGE_LIVE=1 pytest tests/integration   # hits the real claude CLI, costs money
```
