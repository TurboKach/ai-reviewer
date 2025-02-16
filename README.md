# Claude PR Reviewer

An automated code review tool that works with GitHub Actions. 

> 📘 **New to GitHub Actions?**  
> GitHub Actions is like an automated assistant for your repository. When someone opens a pull request, this tool will automatically run and provide AI-powered code review comments. You don't need to run anything manually - just add the files as described below, and the review will happen automatically on every pull request! powered by Anthropic's Claude AI that provides intelligent, context-aware code reviews on your pull requests.

## Features

- 🤖 Automated code review comments on pull requests
- 🎯 Intelligent line-specific suggestions with code examples
- 🔍 Support for file filtering via whitelist and blacklist patterns
- 🔄 Handles both inline comments and general review feedback
- 📝 Generates comprehensive review summaries
- 🚫 Avoids duplicate comments on the same issues

## Setup

The easiest way to add this reviewer to your repository:

1. Copy the entire `.github` folder from this repository to your repository. This folder contains everything needed:
   - `.github/workflows/pr-review.yml` - the GitHub Action configuration
   - `.github/scripts/pr_review.py` - the review script

Alternatively, if you want to set it up manually:

1. Create a folder structure `.github/workflows` and `.github/scripts` in your repository
2. Create a file `.github/workflows/pr-review.yml` with this content:

```yaml
name: Claude PR Review
on:
  pull_request:
    types: [opened, synchronize]

jobs:
  review:
    runs-on: ubuntu-latest
    permissions:
      pull-requests: write
      contents: read
    
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
          
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'
          
      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install anthropic PyGithub
          
      - name: Run PR Review
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          # Optional: Configure file filters
          PR_REVIEW_WHITELIST: "*.py,*.js,*.tsx"  # Review only specific files
          PR_REVIEW_BLACKLIST: "tests/*"          # Exclude test files
        run: python .github/scripts/pr_review.py
```

2. Create `.github/scripts/pr_review.py` with the provided reviewer script

Your repository structure should look like this:
```
your-repository/
├── .github/
│   ├── workflows/
│   │   └── pr-review.yml    # Action configuration
│   └── scripts/
│       └── pr_review.py     # Review script
├── [your other files and folders]
```

3. Add your Anthropic API key to your repository secrets:
   - Go to Settings > Secrets and variables > Actions
   - Add a new secret named `ANTHROPIC_API_KEY`
   - Set the value to your Claude API key

## Configuration

### File Filtering

You can control which files are reviewed using whitelist and blacklist patterns:

```yaml
env:
  PR_REVIEW_WHITELIST: "*.py,*.js,src/*"  # Files to include
  PR_REVIEW_BLACKLIST: "tests/*,*.test.js" # Files to exclude
```

- Whitelist: Comma-separated glob patterns of files to review
- Blacklist: Comma-separated glob patterns of files to exclude
- If no whitelist is specified, all files are reviewed by default
- **Blacklist takes precedence over whitelist**

Example patterns:  
- `*` - All files
- `*.py` - All Python files
- `src/*.js` - JavaScript files in the src directory
- `frontend/**/*.tsx` - All TypeScript React files in frontend directory
- `tests/*` - Exclude all files in tests directory
- `*.test.js,*.spec.ts` - Exclude test files

### Review Output

The action provides:
1. Inline comments on specific code lines with suggestions
2. A summary comment including:
   - List of reviewed files
   - List of skipped files
   - Number of suggestions
   - General comments for issues that couldn't be tied to specific lines

## Development

Requirements:
- Python 3.11+
- `anthropic` package
- `PyGithub` package

Local setup:
```bash
pip install anthropic PyGithub
```

## License

MIT License - feel free to use and modify for your needs.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.
