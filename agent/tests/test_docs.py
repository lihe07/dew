"""The planner's documentation: the index, reading a topic or a section, and searching."""

from stardew_agent import docs


def make(tmp_path):
    (tmp_path / "farming.md").write_text("# Farming\nTill, plant, and water.\n\n## water\nwater(area=None) waters thirsty crops.\nRaises NotEnough when the can is empty.\n\n## till\ntill(area) hoes bare ground.\n")
    (tmp_path / "fishing.md").write_text("# Fishing\nfish() and where to fish.\n\n## fish\nfish(casts=3) returns what was caught.\n")
    return tmp_path


def test_topics_form_a_tree_by_path(tmp_path):
    folder = make(tmp_path)
    (folder / "farming").mkdir()
    (folder / "farming" / "crops.md").write_text("# Crops\nWhat grows when.\n\n## spring\nParsnips take 4 days.\n")
    assert list(docs.topics(folder)) == ["farming", "farming/crops", "fishing"]
    assert docs.read("farming/crops#spring", folder) == "## spring\nParsnips take 4 days."
    assert docs.grep("parsnips", folder) == "farming/crops#spring: Parsnips take 4 days."
    assert "try: farming, farming/crops" in docs.read("farming/soil", folder)


def test_read_a_topic_or_a_section(tmp_path):
    folder = make(tmp_path)
    assert docs.read("farming", folder).startswith("# Farming")
    assert docs.read("farming#water", folder) == "## water\nwater(area=None) waters thirsty crops.\nRaises NotEnough when the can is empty."
    assert "its sections are: water, till" in docs.read("farming#harvest", folder)
    assert "try: farming, fishing" in docs.read("mining", folder)


def test_search_finds_lines_with_their_topic_and_section(tmp_path):
    folder = make(tmp_path)
    assert docs.grep("notenough", folder) == "farming#water: Raises NotEnough when the can is empty."
    assert docs.grep("fish\\(", folder).startswith("fishing: fish() and where to fish.")
    assert docs.grep("[unclosed", folder) == "nothing matches '[unclosed'"  # not a valid regex: searched as text


def test_the_real_docs_have_titles_and_summaries():
    for name, path in docs.topics().items():
        title, about = docs.summary(path)
        assert title and about, name


def test_every_link_in_the_docs_and_the_prompt_resolves():
    import re
    from pathlib import Path
    here = Path(docs.__file__).parent
    texts = {"prompts/planner.md": (here / "prompts" / "planner.md").read_text()} | {f"docs/{name}": path.read_text() for name, path in docs.topics().items()}
    broken = []
    for where, text in texts.items():
        for link in re.findall(r'docs\("([^"]+)"\)', text):
            answer = docs.read(link)
            if answer.startswith(("no topic", "no section")):
                broken.append(f"{where} → {link}")
    assert not broken, broken
