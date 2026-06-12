mkdir .github\ISSUE_TEMPLATE
@"
---
name: Bug report
about: Something not working as expected
---

**Which component:** Azure Function / Power Apps / Copilot Studio / Other

**Steps to reproduce:**

**Expected behaviour:**

**Actual behaviour:**

**Run ID (if applicable):**

**Azure Function logs (if available):**
"@ | Out-File -FilePath ".github\ISSUE_TEMPLATE\bug_report.md" -Encoding utf8