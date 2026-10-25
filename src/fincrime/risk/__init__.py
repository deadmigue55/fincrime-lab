"""Module ``risk``: what does fraud cost, and how sure are we? (docs/fraud-loss.md)

Two questions, one data set:

* **Maturation** (``triangle``, ``delay``, ``backtest``): fraud is only known once the victim
  reports it, weeks later. The newest months therefore look cleaner than they are. This is the
  claims-reporting-lag problem of non-life insurance, and it is solved the same way: development
  triangles, chain ladder, Bornhuetter-Ferguson and an explicit delay distribution.
* **Loss** (``lossmodel``): frequency x severity, compounded by FFT into an annual aggregate loss
  distribution, from which expected loss, 99.5% value at risk, capital and stop-loss cover follow.

``simcheck`` repeats the whole pipeline on 200 independent simulated banks, where the answer is
known, to show that the intervals mean what they say.
"""
