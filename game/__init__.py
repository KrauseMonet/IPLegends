"""The game on top of the pipeline: the draft loop and the ball-by-ball simulator.

SPEC 9 listed both as out of scope for phase 1. That boundary was moved deliberately once
the ratings became real -- a rating nothing consumes cannot be judged, and the simulator is
the only consumer that can say whether a number is any good. Nothing in `etl` imports this
package; the dependency runs one way, so the pipeline stays exactly as testable as it was.
"""
