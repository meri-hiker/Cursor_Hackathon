"""Alert-hit screener that follows the review pipeline and the identity SOP.

Pipeline, per hit:

1. Join the hit to its alert and to the practice label, if there is one.
2. Score the screened name, date of birth, country and city against the hit
   (the existing baseline in ``Identity_matching_results 2/score_matches.py``).
3. Follow ``account_ref`` or ``login_handle`` (or ``email``) into Accounts and
   look for red flags: joint holders, several accounts, other login handles
   on the same phone and opening date.
4. Accounts to Onboarding to the KYC applicant.
5. KYC checks and KYC notes: verified name, date of birth and document
   country, and red flags such as different dates of birth over time.
6. Joint accounts and linked records: compare every customer the account
   leads to, by date of birth, nationality, address, email and phone.

Then decide ``SAME_ENTITY``, ``DIFFERENT_ENTITY`` or ``REVIEW`` with the SOP.
"""

from same_person.screener.pipeline import HitResult, run_screener

__all__ = ["HitResult", "run_screener"]
