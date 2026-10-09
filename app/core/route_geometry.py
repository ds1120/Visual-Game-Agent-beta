"""Grid line-of-sight primitives; include both side cells at diagonal corners.

Concept references (independent implementation):
https://www.redblobgames.com/grids/line-drawing/
https://api.nav2.org/nav2-jazzy/html/md_nav2_regulated_pure_pursuit_controller_README.html
"""
import math


def supercover_cells(start, end):
    """Traverse every touched cell with a DDA, including corner-adjacent cells."""
    x,y=map(math.floor,start);ex,ey=map(math.floor,end)
    dx,dy=end[0]-start[0],end[1]-start[1]
    sx=1 if dx>0 else -1;sy=1 if dy>0 else -1
    tx=((x+1-start[0]) if dx>0 else (start[0]-x))/abs(dx) if dx else math.inf
    ty=((y+1-start[1]) if dy>0 else (start[1]-y))/abs(dy) if dy else math.inf
    stepx=1/abs(dx) if dx else math.inf;stepy=1/abs(dy) if dy else math.inf
    yield x,y
    while (x,y)!=(ex,ey):
        if min(tx,ty)>=1-1e-10:
            yield ex,ey
            return
        if abs(tx-ty)<1e-10:
            yield x+sx,y
            yield x,y+sy
            x+=sx;y+=sy;tx+=stepx;ty+=stepy
        elif tx<ty:
            x+=sx;tx+=stepx
        else:
            y+=sy;ty+=stepy
        yield x,y


def corridor_clear(start, end, grid, clearance=None, minimum=0):
    h,w=grid.shape
    for x,y in supercover_cells(start,end):
        if not (0<=x<w and 0<=y<h) or not grid[y,x]:return False
        if clearance is not None and clearance[y,x]<minimum:return False
    return True


def shortcut_cost(start, end, clearance, center_weight):
    """Compare a shortcut with the existing path using the same wall penalty."""
    cells=list(supercover_cells(start,end))
    penalty=sum(1+center_weight/(float(clearance[y,x])+.5) for x,y in cells)/len(cells)
    return math.hypot(end[0]-start[0],end[1]-start[1])*penalty


def follows_route(start, end, intermediate, tolerance=.5):
    """Limit a straight click to half a grid cell from the planned polyline."""
    dx,dy=end[0]-start[0],end[1]-start[1]
    squared=dx*dx+dy*dy
    for x,y in intermediate:
        t=max(0.,min(1.,((x-start[0])*dx+(y-start[1])*dy)/squared)) if squared else 0.
        if math.hypot(x-start[0]-t*dx,y-start[1]-t*dy)>tolerance:return False
    return True
