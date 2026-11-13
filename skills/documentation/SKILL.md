---
name: documentation
description: The conductor's rules for what this work writes into the project's documents and comments, and for changing or deleting documents when a task group is closed. The conductor loads it before it writes its first task spec in a session. Not for sessions on other models.
user-invocable: false
---

# Documents and comments

These rules decide what this work writes down outside the code, and when.

- **Comments** are written inside the task that writes the code.
- **Documents** are changed once per task group, when the group is closed. Implementation tasks do not edit them.

Reason: when every related task is done, a document statement is rewritten once, against the code as it now is. The subagents that implement do not have to judge documents.

The rules cover the project's own documents and comments: README files, files under `docs/`, instruction files such as `CLAUDE.md`, plans and specs kept in the repository, and comments in the code.

They do not cover:

- **The work folder under `.orchestra/`.** Those are working records with their own formats. No later work reads them, so a fact that has to outlive this work must not stay only there.
- **Documents for people who do not read the code**: a user guide, API reference for a library's users, instructions for operators. Keep them true as the work changes what they describe, but do not apply the test below to them.
- **A history the project keeps on purpose**, such as a changelog. Leave it as it is, and add to it only when the project's convention says to.
- **A text file that the product ships or runs**, such as a prompt, a template, or a skill. For these rules it is code. Name it in a task spec like any other file.

## The test

**Without this text, would a capable reader who has only the code make a wrong decision?**

If the answer is no, do not write the text. Existing text that this work turns into a "no" is deleted.

A fact can pass the test only when it came from outside the code: the user said it, something was run and measured, or an outside system behaved that way. A fact you learned by reading the code is already in the code.

Put a fact in the first place in this table that can hold it.

| The fact | Where it goes |
|---|---|
| What the code does, how it is structured, how its parts connect | Nowhere. The code says it. When the code does not say it clearly, the fix is in the code: a better name, a type, a test |
| A reason that belongs to one place in the code and that the code cannot show: why the obvious approach is not used here, a workaround for an outside system, an order that must not change | A comment at that place, a few lines at most |
| A fact that holds across the project and has no one place: what the product is for, a constraint from outside the code, what is planned and not yet built | A document the project already has for that kind of fact |
| What changed and why it changed | Git. Not a comment and not a document |

A document says what is true now. When a fact changes, rewrite the statement. Do not append to it, and do not leave "previously", a date, or a note that it was updated.

## When you write an implementation task spec

`orchestra:opus-engineer` and `orchestra:sonnet-builder` carry two standing instructions. They do not create or edit a document unless the task spec names the file. They write a comment only for a reason that belongs to that exact place and that the code cannot show. Do not repeat these in the task spec.

Add only what belongs to this task: go through `## Constraints` in the brief and through the decision log for reasons that belong to one place in this task's code and pass the test. Write each one under `## Context`, and say that it goes in a comment at that place.

## When you review an implementation task

Add these checks to your independent review, before the verdict. Record them under `## Checks run`.

- A diff that creates or edits a document the task spec did not name fails the review.
- Read every comment line that the diff adds, and apply the test. These fail the review: a comment that says what the code does, and a comment that tells the history of the change ("now uses", "changed from", a task ID, a reference to the brief).
- Where the task spec gave a reason for a comment, confirm that the comment is at that place.

After you read the report, keep what the close of the group will need. Write one line in the decision log, `- [T02] For the close of G1: the fact.`, for each of these:

- A document statement that the report lists under `## Found outside scope` as made false by the change.
- A fact that passes the test, such as a measurement or the behavior of an outside system.

## When you close a task group

Close a group when every task in it has passed review. Work from the result, not from what you expected at design time.

1. Read the group's combined diff, `## Constraints` in the brief, and the decision log lines for this group.
2. Answer three questions about the group.
   1. **What did the group make false?** Search the documents and the comments for the names, commands, paths, and behaviors that the group changed or removed. Each statement that is now false is rewritten or deleted.
   2. **What did the group make unnecessary?** A plan item or a spec section that the group implemented is in the code now, so it is deleted. If part of it is a fact that passes the test, that part stays, or moves to a comment, and the rest goes.
   3. **What does the group know that the code does not show?** Each fact from the brief or the decision log that will still be true after this work, passes the test, and is not yet in a comment or a document goes where the table says.
3. If all three answers are empty, write `- [G1] Closed. Documents: no change.` in the decision log. The group is closed, and there is no docs task.
4. Otherwise write a docs task spec for the group. It is one more task in that group, with its own task ID.
   - Under `## Scope and limits`, list each file, each statement in it, and what happens to the statement: it is deleted, or it is rewritten to say a thing that you state. You decide what the document says. The subagent writes it. End the list with "Create no other document."
   - Under `## Acceptance criteria`, add a check for each change. For a removed name, a search that must find nothing is enough, for example `grep -rn "old_name" README.md docs/`.
   - Before the spec has a subagent delete document text, confirm that git tracks the file: `git ls-files --error-unmatch <path>`. Git is what keeps the old text. If the file is not tracked, do not delete from it. Ask the user.
   - Choose the subagent by `orchestra:delegation`, delegate, and review. When the task passes review, add it to the group's line and write `- [G1] Closed. Docs task T04.` in the decision log.

## When you review a docs task

- Run the search from question 1 yourself, on the result. A false statement that is still there fails the review.
- Read every line that the diff adds, and apply the test. These fail the review: text that repeats what the code says, text that tells the history of the change, and a document or a section that the task spec did not ask for.

In the final report, say which document text this work deleted or rewrote.

## What is inside this work and what is an extra

Inside this work, with no question to the user:

- Text that this work's own changes make false or unnecessary, wherever it is in the project.
- Facts this work learned that pass the test.

An extra, which follows the conductor's rule for extras:

- A statement that was already false before this work, or that this work did not cause.
- Merging, restructuring, or rewriting documents. Removing or rewriting comments that this work did not make false.
- Deleting a whole document file, or creating a new one. This includes the case where a fact needs a document and the project has none for it.
