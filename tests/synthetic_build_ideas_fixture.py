"""Synthetic build-idea fixtures, with placeholder projects and prose."""

from practicegraph.analysis.build_ideas import BuildEdition, BuildIdea, RepoPick

SAMPLE_BUILD_IDEAS = tuple(
    BuildIdea(**row)
    for row in [
        {
            "idea_id": "example-idea-1",
            "api": "claude",
            "feature": "Synthetic example for build idea tests.",
            "title": "Synthetic example for build idea tests.",
            "hook": "Synthetic example for build idea tests.",
            "summary": "Synthetic example for build idea tests.",
            "steps": ("Read the example input.", "Check the example output."),
            "why": "Synthetic example for build idea tests.",
            "url": "https://example.com/ideas/1",
            "source": "Synthetic example for build idea tests.",
            "repo": None,
        },
        {
            "idea_id": "example-idea-2",
            "api": "claude",
            "feature": "Synthetic example for build idea tests.",
            "title": "Synthetic example for build idea tests.",
            "hook": "Synthetic example for build idea tests.",
            "summary": "Synthetic example for build idea tests.",
            "steps": ("Read the example input.", "Check the example output."),
            "why": "Synthetic example for build idea tests.",
            "url": "https://example.com/ideas/2",
            "source": "Synthetic example for build idea tests.",
            "repo": None,
        },
        {
            "idea_id": "example-idea-3",
            "api": "claude",
            "feature": "Synthetic example for build idea tests.",
            "title": "Synthetic example for build idea tests.",
            "hook": "Synthetic example for build idea tests.",
            "summary": "Synthetic example for build idea tests.",
            "steps": ("Read the example input.", "Check the example output."),
            "why": "Synthetic example for build idea tests.",
            "url": "https://example.com/ideas/3",
            "source": "Synthetic example for build idea tests.",
            "repo": None,
        },
        {
            "idea_id": "example-idea-4",
            "api": "openai",
            "feature": "Synthetic example for build idea tests.",
            "title": "Synthetic example for build idea tests.",
            "hook": "Synthetic example for build idea tests.",
            "summary": "Synthetic example for build idea tests.",
            "steps": ("Read the example input.", "Check the example output."),
            "why": "Synthetic example for build idea tests.",
            "url": "https://example.com/ideas/4",
            "source": "Synthetic example for build idea tests.",
            "repo": None,
        },
        {
            "idea_id": "example-idea-5",
            "api": "openai",
            "feature": "Synthetic example for build idea tests.",
            "title": "Synthetic example for build idea tests.",
            "hook": "Synthetic example for build idea tests.",
            "summary": "Synthetic example for build idea tests.",
            "steps": ("Read the example input.", "Check the example output."),
            "why": "Synthetic example for build idea tests.",
            "url": "https://example.com/ideas/5",
            "source": "Synthetic example for build idea tests.",
            "repo": None,
        },
        {
            "idea_id": "example-idea-6",
            "api": "openai",
            "feature": "Synthetic example for build idea tests.",
            "title": "Synthetic example for build idea tests.",
            "hook": "Synthetic example for build idea tests.",
            "summary": "Synthetic example for build idea tests.",
            "steps": ("Read the example input.", "Check the example output."),
            "why": "Synthetic example for build idea tests.",
            "url": "https://example.com/ideas/6",
            "source": "Synthetic example for build idea tests.",
            "repo": None,
        },
    ]
)
SAMPLE_REPO_PICKS = tuple(
    RepoPick(**row)
    for row in [
        {
            "repo_id": "example-repo-1",
            "name": "Example reference",
            "url": "https://github.com/example/example-1",
            "what": "Example reference",
            "why": "Example reference",
            "caveat": "Example reference",
        },
        {
            "repo_id": "example-repo-2",
            "name": "Example reference",
            "url": "https://github.com/example/example-2",
            "what": "Example reference",
            "why": "Example reference",
            "caveat": "Example reference",
        },
        {
            "repo_id": "example-repo-3",
            "name": "Example reference",
            "url": "https://github.com/example/example-3",
            "what": "Example reference",
            "why": "Example reference",
            "caveat": "Example reference",
        },
    ]
)
SAMPLE_EDITION = BuildEdition(ideas=SAMPLE_BUILD_IDEAS, repos=SAMPLE_REPO_PICKS)
