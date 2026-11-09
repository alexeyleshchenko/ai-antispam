"""The two questions `group_administrators` answers, each as one SQL fragment.

Why this module exists
----------------------
`group_administrators` carries two booleans and they are not interchangeable:

  is_registered  the human opened the bot, so this row is the BILLING LINK and
                 the reason the group appears in their `/stats`.
  is_admin_now   Telegram still lists them as an admin of that chat.

Before this module, "which seats may we charge" was written out five times
across `group_operations.py` and `scripts/recover_failed.py`, in three
different shapes. The drift was not cosmetic:

  * `recover_failed.credits_available` called itself a "read-only twin of
    deduct_credits_from_admins: same join, same ordering" and carried NEITHER
    flag, so its dry run counted a departed stranger's balance as payable and
    over-stated the delete list.
  * every other site filtered `is_registered` alone, so an admin who had left
    the group was still the payer (measured 2026-11-09: 2 such seats live, and
    one of them had been funding a group it left weeks earlier).

Two predicates, never one
-------------------------
There are TWO questions here and they must not collapse into one string,
because their failure directions are opposite:

  BILLABLE/PAYING  answers "who do we charge". It must fail CLOSED: a sweep
                   hiccup that leaves a stale `is_admin_now = TRUE` costs one
                   extra day of billing, and the next sweep stops it. Charging
                   somebody who left is the worse error.
  REGISTERED_SEAT  answers "which groups does this human own" (`/stats`, the
                   payment restore, the group-id lookups). It must fail OPEN:
                   filtering `/stats` on `is_admin_now` makes a group vanish
                   from its owner's list on a single failed rights read, which
                   is user-visible and flaps.

Collapsing them is exactly how #130 happened: one flag answering three
questions made a departed admin the payer AND a live Telegram admin invisible.

Alias contract
--------------
The fragments name their aliases, so a composing query MUST use them:

  ga  -> group_administrators
  a   -> administrators   (only PAYING_SEAT needs it)

NULL: both columns are `NOT NULL DEFAULT TRUE` (`trust_operations.py:101,105`),
so `= TRUE` is exact here and no COALESCE is owed. A row written before the
columns existed reads TRUE, which preserves the pre-#130 behaviour in both
directions.
"""

# The billing link alone: the human opened the bot. Answers ownership questions.
REGISTERED_SEAT = "ga.is_registered = TRUE"

# A seat we may charge: the human opened the bot AND is still an admin there.
BILLABLE_SEAT = "ga.is_registered = TRUE AND ga.is_admin_now = TRUE"

# The same seat, funded. `deduct_credits_from_admins` deliberately does NOT use
# this one: it compares the balance against the amount it is about to charge, so
# a `credits > 0` filter there would answer a different question and hide the
# insufficient-balance path that deactivates a group.
PAYING_SEAT = f"{BILLABLE_SEAT} AND a.credits > 0"
