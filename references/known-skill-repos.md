# Known Skill Repositories

Curated list of skill sources that `skill-router` checks during the catalog-check
phase (see [`catalog-check.md`](./catalog-check.md)).

## Local catalogs (checked first)

| Path | Source | Skills |
|------|--------|--------|
| `~/.claude/skills/` | Your custom + installed Claude skills | varies |
| `~/.agent/skills/` | [Antigravity Awesome Skills](https://github.com/sickn33/antigravity-awesome-skills) | 1,400+ |
| `~/.composio-skills/composio-skills/` | [Composio integrations](https://github.com/ComposioHQ) | 940+ |

## Remote catalogs (checked when no local match)

| Repository | Skills Path | API Enumeration URL | Focus |
|------------|-------------|---------------------|-------|
| `anthropics/skills` | `/skills/` | `https://api.github.com/repos/anthropics/skills/contents/skills` | Official Anthropic |
| `K-Dense-AI/claude-scientific-skills` | `/scientific-skills/` | `https://api.github.com/repos/K-Dense-AI/claude-scientific-skills/contents/scientific-skills` | 139 scientific skills |
| `ComposioHQ/awesome-claude-skills` | `/` | `https://api.github.com/repos/ComposioHQ/awesome-claude-skills/contents` | Curated awesome list |
| `sickn33/antigravity-awesome-skills` | `/skills/` | `https://api.github.com/repos/sickn33/antigravity-awesome-skills/contents/skills` | Frontend, design, copy, SEO |
| `intellectronica/agent-skills` | `/skills/` | `https://api.github.com/repos/intellectronica/agent-skills/contents/skills` | Includes `ultrathink`, deep-thinking patterns |
| `wasabeef/claude-code-cookbook` | `/plugins/*/skills/` | `https://api.github.com/repos/wasabeef/claude-code-cookbook/contents/plugins` | Cookbook + ultrathink |

## Search order (enforced by [`catalog-check.md`](./catalog-check.md))

```
1. Local — ls/grep in the three paths above
2. Remote known repos — WebSearch then API enumeration if no hit
3. Broader GitHub — site:github.com SKILL.md "allowed-tools" <keywords>
4. Fallback — superpowers:writing-skills generates a custom skill
```

## Adding a new repo

PRs welcome. To qualify, a repository should:

- Contain at least 5 SKILL.md files at predictable paths
- Use the standard YAML frontmatter (`name`, `description`, `allowed-tools`)
- Be MIT/Apache/CC-BY licensed (so router can suggest installation freely)
- Be actively maintained (commits in the last 90 days)

Submit via PR adding a row to the table above, with a short note on what
domain the repo covers.
