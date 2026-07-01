import os

def main():
    skills_dir = "/Users/winston/.agents/skills"
    readme_path = os.path.join(skills_dir, "README.md")
    
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

    tree_text = "\n".join(tree_lines) + "\n\n"

    if not os.path.exists(readme_path):
        print(f"README.md not found at {readme_path}")
        return

    with open(readme_path, 'r', encoding='utf-8') as f:
        readme_content = f.read()

    start_marker = "## 🌲 Cấu Trúc Thư Mục Chi Tiết (Detailed Directory Tree)"
    end_marker = "## 📂 Chi Tiết Phân Loại Hệ Thống (Directory Structure)"

    if start_marker in readme_content and end_marker in readme_content:
        start_idx = readme_content.find(start_marker) + len(start_marker)
        end_idx = readme_content.find(end_marker)
        
        new_content = readme_content[:start_idx] + "\n" + tree_text + readme_content[end_idx:]
        
        with open(readme_path, 'w', encoding='utf-8') as f:
            f.write(new_content)
        print(f"Successfully updated README.md tree with {total_skills} skills.")
    else:
        print("Error: Could not find start or end markers in README.md")

if __name__ == "__main__":
    main()
