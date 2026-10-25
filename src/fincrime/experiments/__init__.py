"""Module ``experiments``: did the new rule actually help? (docs/experiment-lab.md)

A/B tests of fraud rules are hard in three ways, and this module takes each in turn on a bank where
the true answer is known:

* **The outcome is rare and heavy-tailed** (``design``, ``analysis``): a few hundred fraud claims
  across a hundred thousand customers per arm, a few of them very large. Power, the minimum
  detectable effect, variance reduction (CUPED) and the choice of test follow from that.
* **The outcome arrives late** (``sequential``): fraud is reported weeks after it happens, and
  nobody waits for the end. Peeking inflates false positives; group-sequential boundaries and
  always-valid tests do not, and the label delay of step 7 decides how early a readout can be read.
* **Comparisons are not always randomised** (``bias``): rolling a rule out to new customers first,
  or reading a ratio metric whose denominator the rule changes, gives a confident wrong answer.

``worlds`` supplies the truth: the same bank built under two rule sets, so each customer's outcome
under both is known and an experiment's estimate can be scored against the real effect.
"""
