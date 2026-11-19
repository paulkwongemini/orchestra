## Handing work to Haiku

Two subagents run on Haiku, which costs a small fraction of what you cost. They take work that needs no judgment, and they are the only subagents you may start. Use one whenever every condition for it holds. When one condition does not hold, do the work yourself.

**`orchestra:haiku-scout` finds and reads.** Use it when all of these are true:

1. The answer is in the files or in a command's output as it stands: where something is, which one it is, what a line says, a list.
2. Opening the lines it cites shows whether the answer is right.
3. The material to go through is much larger than the answer, and you will not need that material afterwards. Read a file you are about to edit yourself. Run a search yourself when you already know where to look.

**`orchestra:haiku-editor` makes a mechanical edit.** Use it when all of these are true:

1. The request alone determines the resulting text. Anyone who followed it would produce the same diff.
2. You can list the files by path, and every one is a file your task spec lets you edit.
3. A command fails when the edit is wrong or incomplete.
4. The same edit is needed in three or more places. Make one or two edits yourself.

Never hand Haiku:

- A judgment: whether something is good, why something fails, which option is better, what a design means.
- A check that decides whether work is accepted. Run your acceptance commands yourself.
- A question where the answer "none" or "that is all of them" matters. When your result depends on having found everything, run the one decisive search yourself.

Reason: the result has to be the same as if you had done all of the work yourself. Haiku's output is safe only where you can check it against the files in less work than doing it.

The prompt is the whole request, in one of these two forms and at most 1,500 characters. The harness refuses any other form.

```
FIND: the question, in one or two sentences
IN: the paths or directories to look in (optional)
```

```
DO: the edit, stated so that it leaves no choice
FILES: the paths, separated by commas
CHECK: the command that fails when the edit is wrong or incomplete
```

What comes back is a claim until you have checked it.

- From the scout: open the lines it cites before you rely on them. When they do not say what it claims, or it reports `NOT FOUND`, look yourself.
- From the editor: read the diff of the files, confirm that nothing outside `FILES` changed and that the change is what `DO` says, and run `CHECK` yourself.
- When a result fails your check, do the work yourself. Do not send it back to Haiku.

Work that Haiku did inside your task is your work. Your report and your verification record cover it like the rest.
