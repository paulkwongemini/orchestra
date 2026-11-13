---
name: interview
description: The conductor's procedure for finding out what the user wants before any work starts: one question per turn, each next question decided by the last answer, then the brief. The conductor loads it when it starts to understand a request that produces a deliverable. Not for sessions on other models.
user-invocable: false
---

# Understanding the intent

The interview ends in a brief that lets you make later decisions the way the user would. A user cannot be understood in one turn, and the finer the work, the less likely it is. So ask one thing, take in the answer, and only then decide what to ask next.

## 1. Find the facts yourself

- Before any question, read the codebase and the project's documents, and search the web when that helps.
- A fact is something the environment can settle. A decision is something only the user can settle. Never ask the user for a fact. Never answer a decision for the user.
- Scale the effort to the request. For a small request, this can take a minute and leave nothing to ask.

## 2. Lay out the decisions as a tree

- List the decisions that are still open. Some decisions hang off others: the answer to one changes which others exist, or what their options are.
- Order them so that a decision comes before the decisions that depend on it. Among those that can be asked now, start with the one whose answer changes the most of the rest.
- Keep the tree in your head or in your reply's reasoning. Do not write it to a file and do not show the whole tree to the user.

## 3. Ask one question per turn

- Ask one question in a turn, then stop and wait for the answer. With the question tool (AskUserQuestion), that is one call with one question in it. Do not put two questions in one call, and do not ask a second question in the text around it.
- Give with each question the options, the option you recommend and why in one line, and what changes depending on the answer.
- Ask in the user's language, in plain words. Do not ask the user to choose between names from the code that they have not seen.

Reason: a question asked together with another is asked before its answer could shape it.

## 4. Take in the answer, then decide the next question

- After each answer, say in one line what is now settled.
- Rework the tree: drop the questions the answer made pointless, add the ones it opened, and look up any new facts it calls for.
- An answer that does not fit the options, or that questions the question, is information. Follow it instead of repeating the question.

## 5. When to stop asking

- Stop when every decision still open is one you may make: an implementation detail inside the scope, easy to undo. The test is the one in the conductor's instructions under "When to ask the user".
- Write each of those decisions into the brief as an assumption, under the heading it belongs to, so the user can see it and change it.
- There is no fixed number of questions. Some requests need none, some need many. If the user says to stop asking and get on with it, stop, and write the remaining open decisions into the brief as assumptions.

Reason: every question costs the user a turn. A question whose answer would not change the design or the acceptance criteria is not worth one.

## 6. Write the brief

- Write `.orchestra/<work>/brief.md` only now, after the last answer. Do not write it while questions are still open.
- Its format is in the conductor's instructions under "Document formats". Write it in the user's language.
- A good brief lets you make most later decisions the way the user would.

## 7. Show the brief and ask the last question

- If a tool that shows a file to the user is available (in the Claude desktop app, the one that opens a file in a side pane), open the brief with it. If there is none, give the path of the brief.
- In the chat, write two or three lines: what will be built, the assumptions you made without asking, and any extras the brief proposes. Do not paste the brief.
- Then ask the one fixed last question, alone in its own turn: "Do you want me to wait until you confirm the brief?" Offer two options: start now, or review the brief first.
- Do not ask a separate question such as "Is my understanding correct?". The brief is what the user confirms.
  Reason: a yes to a summary confirms nothing. The user can only judge the brief itself.
- If they choose to start, go on to design. If they choose to review, wait. When they ask for a change, change the brief, show it again, and ask the last question again.
- The harness refuses the delegation until the user has replied after the brief last changed.
