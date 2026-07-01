import os
import re

def main():
    skills_dir = os.path.dirname(os.path.abspath(__file__))
    readme_path = os.path.join(skills_dir, "README.md")
    tree_path = os.path.join(skills_dir, "DIRECTORY_TREE.md")
    
    if not os.path.exists(skills_dir):
        print(f"Skills directory not found: {skills_dir}")
        return

    # Categories we care about
    MACRO_CATEGORIES = [
        "ai-and-data", "andruia", "business-and-finance", "devops-and-security",
        "engineering", "marketing-and-seo", "product-and-design",
        "productivity-and-content", "workflows-and-management"
    ]

    total_skills = 0
    tree_lines = []

    macro_counts = {}
    sub_counts = {}
    structure = {}

    for macro in MACRO_CATEGORIES:
        macro_path = os.path.join(skills_dir, macro)
        if not os.path.exists(macro_path):
            continue
        
        structure[macro] = {}
        macro_skills_count = 0
        
        for sub in sorted(os.listdir(macro_path)):
            sub_path = os.path.join(macro_path, sub)
            if not os.path.isdir(sub_path):
                continue
                
            skills = sorted([d for d in os.listdir(sub_path) if os.path.isdir(os.path.join(sub_path, d)) and not d.startswith(".")])
            sub_skills_count = len(skills)
            
            structure[macro][sub] = skills
            sub_counts[(macro, sub)] = sub_skills_count
            macro_skills_count += sub_skills_count
            
        macro_counts[macro] = macro_skills_count
        total_skills += macro_skills_count

    tree_lines.append(f"\n- 🌌 **[~/.agents/skills](./)** ({total_skills} skills)")
    for macro in MACRO_CATEGORIES:
        if macro not in structure:
            continue
        m_count = macro_counts[macro]
        tree_lines.append(f"  - 📂 **[{macro}](./{macro})** ({m_count} skills)")
        for sub in sorted(structure[macro].keys()):
            s_count = sub_counts[(macro, sub)]
            tree_lines.append(f"    - 📁 *[{sub}](./{macro}/{sub})* ({s_count} skills)")
            for skill in structure[macro][sub]:
                tree_lines.append(f"      - 📄 [{skill}](./{macro}/{sub}/{skill})")

    tree_text = "\n".join(tree_lines) + "\n"

    # 1. Write DIRECTORY_TREE.md
    directory_tree_content = f"""# 🌲 Detailed Directory Tree

This file contains the complete nested structure and direct file links of all registered skills in this library.
{tree_text}"""

    with open(tree_path, 'w', encoding='utf-8') as f:
        f.write(directory_tree_content)
    print(f"Successfully updated DIRECTORY_TREE.md with {total_skills} skills.")

    # 2. Dynamically update skills count in README.md
    if os.path.exists(readme_path):
        with open(readme_path, 'r', encoding='utf-8') as f:
            readme_content = f.read()

        # Format number with commas (e.g., 1,900)
        formatted_count = f"{total_skills:,}"
        
        # Replace the Total Registered Skills line
        updated_content = re.sub(
            r"\*\s+\*\*Total Registered Skills:\*\*\s+\*\*\d+(?:[\.,]\d+)?\*\*",
            f"*   **Total Registered Skills:** **{formatted_count}**",
            readme_content
        )

        with open(readme_path, 'w', encoding='utf-8') as f:
            f.write(updated_content)
        print(f"Successfully updated skills count in README.md to {formatted_count}.")
    else:
        print(f"Warning: README.md not found at {readme_path}")

if __name__ == "__main__":
    main()
