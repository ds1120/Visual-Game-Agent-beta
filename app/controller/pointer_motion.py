"""Time-based minimum-jerk interpolation, with zero speed/acceleration at either end."""
def smooth_point(start, target, progress):
    t = max(0.0, min(1.0, progress))
    ease = t*t*t*(10+t*(-15+6*t))
    return tuple(round(a+(b-a)*ease) for a,b in zip(start,target))
