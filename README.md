# 🌌 Antigravity Skills Library

> [!IMPORTANT]
> **Attribution Notice:** This repository is a restructured fork of [sickn33/antigravity-awesome-skills](https://github.com/sickn33/antigravity-awesome-skills). The original skills are authored by the contributors of the upstream repository. This fork reorganizes the flat folder structure into a categorized multi-tier hierarchy to optimize context search and prevent flat-directory parsing performance bottlenecks for AI agents in Antigravity IDE and other environments.

Welcome to the **Skills Library** for Antigravity IDE & CLI. This repository contains behavior playbooks (`SKILL.md`) that help AI agents perform automated tasks, planning, debugging, and system optimization.

To improve search efficiency and avoid I/O bottlenecks in flat directories, the skills are categorized into a multi-tier folder structure.

---

## 🌲 Detailed Directory Tree

To view the complete list and nested structure of all registered skills with direct file links, please refer to the dedicated [DIRECTORY_TREE.md](./DIRECTORY_TREE.md) file.

## 📂 Directory Structure Details

The library is organized into **9 Macro Categories**, each containing specialized subcategories:

### 1. [ai-and-data](./ai-and-data)
Skills related to Artificial Intelligence, Data Processing, MLOps, RAG, and Large Language Models (LLMs).

### 2. [andruia](./andruia)
Consultancy, expert skill design, and niche intelligence playbooks for Andruia.

### 3. [business-and-finance](./business-and-finance)
Business analysis, finance, Odoo development, legal compliance, and operations.

### 4. [devops-and-security](./devops-and-security)
Security, pentesting, cloud infrastructure management, automation, and CI/CD pipelines.

### 5. [engineering](./engineering)
Software development, algorithms, system architecture, mobile/game development, and codebase management.

### 6. [marketing-and-seo](./marketing-and-seo)
Marketing strategies, SEO, Conversion Rate Optimization (CRO), and social media outreach.

### 7. [product-and-design](./product-and-design)
UI/UX design, aesthetic styles, 3D motion, animation, and frontend performance.

### 8. [productivity-and-content](./productivity-and-content)
Office automation, health and wellness, educational content, and scientific computing.

### 9. [workflows-and-management](./workflows-and-management)
Project management, collaboration workflows, agent execution paths, and technical documentation.

---

## 🎯 Agent Taxonomy & Classification Rules

To help AI agents maintain consistency when adding or moving skills, adhere to the following classification guidelines:

### 1. Domain Mapping Decision Matrix

Refer to the matrix below to select the appropriate **Macro Category** for a new skill:

| Skill Task Domain | Macro Category | Example Subcategories |
| :--- | :--- | :--- |
| AI, LLMs, Prompts, MLOps, Data | `ai-and-data` | `agents-and-orchestration`, `rag-and-search` |
| Consultancy, Niche Intelligence for Andruia | `andruia` | `00-andruia-consultant` |
| Business, Finance, Legal, Odoo ERP | `business-and-finance` | `odoo-development`, `startup-and-business-analysis` |
| AWS/Azure Cloud, Docker, CI/CD, Pentesting, Security | `devops-and-security` | `cybersecurity-and-pentesting`, `azure-cloud` |
| Programming, Languages, Algorithms, DB, Low-level | `engineering` | `languages-and-syntax`, `code-quality-and-refactoring` |
| SEO, Marketing, Copywriting, Social media, CRO | `marketing-and-seo` | `search-engine-optimization`, `marketing-strategy-and-copy` |
| UI/UX Design, Aesthetics, 3D/Motion, Figma | `product-and-design` | `ux-principles-and-design-taste`, `design-systems-and-components` |
| Office tools, Health, Education, Math/Science | `productivity-and-content` | `cloud-and-office-automation`, `scientific-computing` |
| Project management, DDD, Git, Planning, Docs | `workflows-and-management` | `planning-and-execution`, `git-and-github-workflows` |

### 2. Folder Naming Rules
*   **Kebab-case:** All subcategories and skill folder names must be lowercase and separated by hyphens (e.g., `code-quality-and-refactoring`).
*   **Conjunctions (`*-and-*`):** Use `and` to combine closely related concepts (e.g., `languages-and-syntax`). Do not use symbols like `&` or `+`.
*   **Special Prefixes:** Use numerical prefixes for sequential project-specific skills (e.g., `00-andruia-consultant`, `10-andruia-skill-smith`).

### 3. Adding a New Skill
1.  **Select Destination:** Match the task against the matrix to find the correct `Macro/Subcategory` path (e.g., `./ai-and-data/prompt-engineering-group/your-skill`).
2.  **Create Structure:** Create the skill directory and place a structured `SKILL.md` inside it.
3.  **Register in Manifest:** Add the relative path to the `entries` array in [.antigravity-install-manifest.json](./.antigravity-install-manifest.json) in alphabetical order, and update the `updatedAt` timestamp.

---

## 🧭 Classification & Search Principles

### 1. Classification Principles
*   **Context-driven Grouping:** Skills are grouped by their practical application context.
*   **Strict 3-level Hierarchy:** To prevent recursive scanning loops and file I/O bottlenecks, the structure is restricted to 3 levels: `Root` ➔ `Macro Category` ➔ `Subcategory` ➔ `Skill Directory`.
*   **Disk-to-Manifest Sync:** Physical directory structure must always match the index in `.antigravity-install-manifest.json` 100%.

### 2. Search & Resolution Principles
*   **Manifest-First Lookup:** AI agents should load `.antigravity-install-manifest.json` in memory to locate skills instead of recursively scanning the disk with shell tools.
*   **Relative Path Filtering:** Filter the manifest list by keywords (e.g., `react`) to resolve relevant skills.
*   **Identifier Separation:** The calling token (e.g., `@clean-code`) is defined in the frontmatter metadata of `SKILL.md` independently of the physical folder path.

---

## ⚙️ Configuration & Manifest Contract

*   **Source Manifest:** [.antigravity-install-manifest.json](./.antigravity-install-manifest.json)
*   **Total Registered Skills:** **1,900**

---

## 🔗 MCP Integration

To enable AI agents to automatically discover and use these skills, connect them using the **`superpowers`** MCP server:

1.  **Configure `mcp_config.json`:**
    Link the flat directory by specifying `SKILLS_PATH` and `SUPERPOWERS_SKILLS_DIR` in your IDE's MCP config:
    ```json
    "superpowers": {
      "command": "npx",
      "args": ["-y", "superpowers-mcp", "start"],
      "env": {
        "SKILLS_PATH": "~/.agents/flat-skills",
        "SUPERPOWERS_SKILLS_DIR": "~/.agents/flat-skills"
      }
    }
    ```

### 💡 Why is this structure optimized?
*   **Token Efficiency:**
    1.  *MCP Gateway:* The agent loads only the specific `SKILL.md` needed for the active task rather than reading the entire library.
    2.  *Categorization:* Dividing skills into directories allows semantic search tools to target relevant categories, filtering out noise and saving token overhead.

### ❓ Do I need to copy or symlink skills into each project?
*   **No, normally not:** The global `superpowers` MCP server maps the flat skills directory globally. You can invoke any skill using `@<skill-name>` (in the IDE chat) or `/` commands (where supported by CLI).
*   **When to copy/symlink locally:**
    1.  *Team Collaboration (Git):* To share skills with team members in the same repository.
    2.  *Scope Restricting:* To lock agent capabilities to a specific subset of local skills.
    3.  *Environments without MCP:* For runtimes that do not support MCP server installations.

---

## 🚀 How to Use Skills

AI agents map skills by the name defined in the `SKILL.md` frontmatter, independent of the folder path:
*   **IDE Chat:** Use `@<skill-name>` (e.g., `@clean-code`, `@figma-automation`).
*   **CLI Commands:** Use `/` commands (where supported).

## 🔄 Auto-Update Script

Use the automation script to fetch updates from upstream without breaking the categorization structure:
*   **Script Path:** [update_skills.py](./update_skills.py)
*   **Execution Command:**
    ```bash
    python3 ~/.agents/skills/update_skills.py
    ```

### ⚙️ Update Pipeline:
1.  **Shallow Clone:** Clones the upstream awesome-skills repository to a temporary directory.
2.  **In-place Update:** Updates content for existing skills within their current category folders.
3.  **Auto-Classification:** Places new upstream skills into appropriate categories using keyword rules. Unknown skills fall back to `uncategorized-and-misc`.
4.  **Rebuild Manifest:** Re-indexes the directory structure to `.antigravity-install-manifest.json`.
5.  **Rebuild README:** Updates the ASCII directory tree in `README.md`.
6.  **Flat Sync:** Executes `sync_flat_skills.py` to rebuild symlinks in the flat directory for MCP and plugin cache.

---

## 🛡️ Verification

Verify the integrity of the local skills library:
```bash
python3 ~/.agents/skills/verify_exact_skills.py
```
Nếu màn hình trả về:
```text
Manifest has 1900 entries.
SUCCESS: No duplicate entries in manifest.
SUCCESS: Every manifest entry exists on disk!
```
$\rightarrow$ Your skills library is 100% verified.
