## Project Overview

This project is a collection of skills and agents for Claude Code.

## Target Directory

Skills are installed to `$WORKSPACE/.claude/skills/`.

## Sample Code

The `pcie_scsc/` directory contains sample code for verifying skill behavior.

## Skill Authoring Guidelines

Skills are designed to run with small LLM models (e.g., Haiku). Because smaller models have less reasoning capacity, skill prompts must be written with:

- **Explicit, detailed instructions** — leave no ambiguity; spell out every step.
- **Clear structure** — use numbered steps, headers, and bullet points so the model can follow along sequentially.
- **Concrete examples** — show expected inputs and outputs rather than relying on abstract descriptions.
- **Guardrails and constraints** — explicitly state what the model should and should not do.
- **Self-contained context** — avoid relying on implicit knowledge; provide all necessary context within the skill prompt itself.

The goal is to ensure even a small LLM produces high-quality, consistent outputs.
