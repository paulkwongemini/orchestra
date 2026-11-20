---
name: interview
description: The conductor's procedure for finding out what the user wants before any work starts: one question per turn, each next question decided by the last answer, then the brief. The conductor loads it as soon as a request is work, before it takes any action for the request. Not for sessions on other models.
user-invocable: false
---

# Understanding the intent

The interview ends in a brief that lets you make later decisions the way the user would. What a user wants is rarely clear from one message, and the more detailed the work, the less clear it is. So ask one thing, take in the answer, and only then decide what to ask next.

## 1. Find the facts yourself

- Before any question, read the codebase and the project's documents, and search the web when that helps.
- Find facts only with actions that need nobody's approval: reading the project's files, running commands that only read them, and searching the web. Before the brief is confirmed, do not run a command of a kind that the harness stops unless the brief pre-approves it, and do not take any other action that the user has to allow first. Connecting to a server to read its logs is one.
- When a fact can be found only with such an action, leave it unfound. Put the action on the list for step 5, and go on with the questions that do not depend on the fact. Find the fact after the user has confirmed the brief. If it then changes the brief, change the brief and ask the last question again.
  Reason: an action that needs approval, taken before the first question, puts a permission prompt in front of the user before they have said what they want. Step 5 asks for the same permission once, with the reason.
- A fact is something the environment can settle. A decision is something only the user can settle. Never ask the user for a fact. Never answer a decision for the user.
- Work out which kinds of action the work will need that the harness stops unless the brief pre-approves them: changing data or a schema, changing dependencies, changing git state, pushing, changing things on GitHub, writing to another machine over HTTP, using ssh, deploying. Count the fact-finding you left for after the brief, and the commands your own review will run, such as a migration or a seed to check a result, or installing dependencies to run the tests. Read what the project's instruction files, such as `AGENTS.md` and `CLAUDE.md`, say about each of these actions, and about worktrees.
- Scale the effort to the request. For a small request, this can take a minute and leave nothing to ask.

## 2. Write the intent, then lay out the decisions as a tree

- First write for yourself, in one sentence, what the user wants and what it is for. Then check that the sentence says four things:
  - who the result is for,
  - why it is wanted, and why now when that matters,
  - what will show that it worked,
  - the one constraint that limits the work most.
- Check also whether two reasonable aims pull against each other, such as simplicity and flexibility, or cost and speed, and whether the user has said which one wins.
- Fill these from the request and from the facts you found. Each one you cannot fill is an open decision at the root of the tree, because its answer changes which other decisions exist.
- A request that names a familiar form, such as "a dashboard" or "a settings page", often names the form that came to mind, not the result the user needs. Treat the form as one possible answer and the result behind it as the open decision. A form stated together with its purpose is not open.
- For a small, clear request the sentence takes a moment and opens nothing.

Reason: a design question asked before the intent is settled is asked inside a frame that may be wrong, and its answer is wrong with it.

- List the decisions that are still open. Some decisions hang off others: the answer to one changes which others exist, or what their options are.
- Order them so that a decision comes before the decisions that depend on it. Among those that can be asked now, start with the one whose answer changes the most of the rest.
- The tree is your own working aid. Do not write it to a file, and do not show the whole tree to the user.

## 3. Ask one question per turn

- Ask one question in a turn, then stop and wait for the answer. With the question tool (AskUserQuestion), that is one call with one question in it. Do not put two questions in one call, and do not ask a second question in the text around it.
- Give with each question the options, the option you recommend and why in one line, and what changes depending on the answer.
- The option you recommend is your best guess at what the user will answer, from what you found. Say what the guess rests on, so that a wrong guess is easy to correct.
- Ask in the user's language, in plain words. Do not ask the user to choose between names from the code that they have not seen.

Reason: a question asked together with another is asked before its answer could shape it. A user corrects a guess faster than they write an answer from nothing.

- When the intent is the open decision, the first question shows your one-sentence reading of it and names which of the four things it is still missing.
- When the goal is settled but the shape of the result is open, and the user would feel the difference, offer two or three directions as the options of one question.
  - Make them different in kind, not variations on one idea, and include the simplest direction that still meets the goal.
  - For each, say what it costs, which part of it is hardest, and what must be true for it to work.
  - If a direction the user seems to favor is weak, say so and why.
- The directions not chosen go under `## Out of scope` in the brief.

## 4. Take in the answer, then decide the next question

- After each answer, say in one line what is now settled.
- Rework the tree: drop the questions the answer made pointless, add the ones it opened, and look up any new facts it calls for.
- An answer that does not fit the options, or that questions the question, is information. Follow it instead of repeating the question.
- An answer that names a quality instead of a result settles nothing: "scalable", "clean", "robust", "the standard way", "the way most apps do it", "I should probably…". Ask what concrete result the quality is for, or what the user would want if they did not have to justify it to anyone.
- A goal without a measure, such as "faster" or "easier to use", cannot be checked. In the next question, recommend a concrete measure, such as a time limit or a behavior someone can observe. It becomes an acceptance criterion.
- "Whatever you think is best" hands the decision to you. If it is one you may make by the test of step 8, make it and write it into the brief as an assumption. If only the user can make it, such as the goal, the scope, or the acceptance criteria, ask again with two concrete options.
  Reason: a decision that only the user can make is still unmade when it is handed back.

## 5. Settle what the brief pre-approves

The work often runs while the user is away. An action that the brief does not pre-approve stops the work until they come back, so settle these actions now. The keys and what each covers are in the conductor's instructions under "Pre-approved actions".

- Ask about each kind of action the work needs. Name the action, say what it is needed for and what it will be run against, and say what happens if it is not pre-approved: the work stops there and waits.
- One question may list several kinds of action for the user to tick. That is still one question.
- A key goes on the `Pre-approved:` line only when the user approved that kind of action in their own answer. Never write a key as an assumption, and never take "stop asking" or "do whatever it takes" as approval. If the user ends the interview before this is settled, write `Pre-approved: none`, and when you show the brief, name the actions where the work will stop.
- Where the project's instructions require the user's explicit permission for an action, such as a commit or a push, say so in the question and name the rule. Write the answer under `## Decision rights` as the explicit permission for this work.
- Where the project's instructions forbid an action outright, do not ask for it and do not write its key. Put the rule under `## Constraints`. If the work cannot be done without that action, tell the user that the two conflict.
- Pre-approval lets a command run. It does not replace a procedure that the project's instructions require around it. Write that procedure under `## Constraints`.
- Do not ask about a kind of action the work does not need.

Reason: only the user can give this permission, and the interview is the last moment they are sure to be present.

## 6. Settle which files you write yourself

The conductor's instructions say under "Files you write yourself" which kinds of file may go on the brief's `Conductor writes:` line: a file whose wording is the design, and a document in which you decide each statement that changes.

- From what you found in step 1, list the files of those two kinds that the work will change. Include the documents that you expect the close of each task group to change.
- When the list is empty, do not ask. Write `Conductor writes: none`.
- Otherwise ask one question, which may list several files for the user to tick. Name each file, say why you would write it yourself, and say what happens if the user does not approve it: a subagent writes it from a task spec that already holds the text, which costs more tokens and changes nothing in the result.
- The rule of step 5 holds here: a path goes on the line only when the user approved that file in their own answer, and never as an assumption. If the user ends the interview before this is settled, write `Conductor writes: none`.
- Do not offer code, configuration, or tests. They stay with the subagents even when the user would let you write them.
- Where the project's instructions say that a file must not be edited, or who may edit it, do not offer that file.

Reason: the user decides which files go without a second writer and an independent review.

## 7. Settle whether the work uses a worktree

The default is no worktree: every subagent works in the user's working directory. The conductor's instructions say under "Working in one directory" when a worktree is needed.

- When the project's instructions forbid worktrees, do not consider one and do not ask. Put the rule under `## Constraints` and design the work without it. If the work cannot be done without one, tell the user that the two conflict.
- When the work does not need one, do not ask. Write `Worktrees: not used.` under `## Constraints`.
- When the work needs one, ask. Say which part of the work needs it, why running the tasks one after the other does not do it, and what changes if the user says no. Write the answer under `## Constraints`: what the worktree is approved for, or that none is used.
- As in step 5, never write an approval as an assumption. If the user ends the interview before this is settled, the work uses no worktree.

Reason: the user decides whether a second checkout is worth what it costs.

## 8. When to stop asking

- Stop when every decision still open is one you may make: an implementation detail inside the scope, easy to undo. The test is the one in the conductor's instructions under "When to ask the user".
- Check that with a second test: you can predict how the user would answer each question you would still ask. If you cannot, the intent is not settled yet, even when every open decision looks like yours.
- If several answers in a row have not made the intent clearer, stop asking in the same direction. Say what is still unclear, and ask whether to step back to the goal.
- Write each of those decisions into the brief as an assumption, under the heading it belongs to, so the user can see it and change it.
- There is no fixed number of questions. Some requests need none, some need many. If the user says to stop asking and get on with it, stop, and write the remaining open decisions into the brief as assumptions.

Reason: every question costs the user a turn. A question whose answer would not change the design or the acceptance criteria is not worth one.

## 9. Write the brief

- Write `.orchestra/<work>/brief.md` only now, after the last answer. Do not write it while questions are still open.
- Its format is in the conductor's instructions under "Document formats". Write it in the user's language.
- A good brief lets you make most later decisions the way the user would.

## 10. Show the brief and ask the last question

- If a tool that shows a file to the user is available (in the Claude desktop app, the one that opens a file in a side pane), open the brief with it. If there is none, give the path of the brief.
- In the chat, write two or three lines: what will be built, the assumptions you made without asking, any extras the brief proposes, which kinds of action the brief pre-approves, and which files you will write yourself. Do not paste the brief.
- Then ask the one fixed last question, alone in its own turn, whatever the user said about asking: "Do you want me to wait until you confirm the brief?" Offer two options: start now, or review the brief first. When the interview left you nothing to ask, this is the only question.
- Do not ask a separate question such as "Is my understanding correct?". The brief is what the user confirms.
  Reason: a yes to a summary confirms nothing. The user can only judge the brief itself.
- If they choose to start, go on to design. If they choose to review, wait. When they ask for a change, change the brief, show it again, and ask the last question again.
