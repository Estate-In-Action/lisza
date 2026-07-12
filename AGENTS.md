# LISZA — Agent Guide

## Development Standards

These rules apply to all work in this project. The workspace-wide source of
truth is `/home/workspace/AGENTS.md` → "Development Standards"; this file
mirrors it so the standard travels with the project.

### Git Workflow
- Create feature branches for all changes.
- Commit frequently with descriptive messages.
- Do NOT push directly to the `main` branch — ask first, and let's talk about
  the best approach.
- Add and commit automatically when tasks complete.

### Documentation
- Update `README.md` when adding new features.
- Add inline comments for complex logic.
- Generate API docs for new endpoints.
- Keep change logs updated.

### Testing Requirements
- Write tests for all new features.
- Run existing tests before completing tasks.
- Focus on end-to-end tests over unit tests.
- Use test-driven development for complex features.
