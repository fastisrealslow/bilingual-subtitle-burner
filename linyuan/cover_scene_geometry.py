"""Measured blank-edge exclusions for native covers, never picture approval."""


def border_exclusions(frames):
    from source_geometry import black_border_crop
    width,height,x,y=black_border_crop(frames)
    original_height,original_width=frames[0].shape[:2]
    marks=[]
    if x:marks.append((0,0,x/original_width,1))
    if x+width<original_width:marks.append(((x+width)/original_width,0,1,1))
    if y:marks.append((0,0,1,y/original_height))
    if y+height<original_height:marks.append((0,(y+height)/original_height,1,1))
    return marks,dict(method='all_samples_uniform_black_edges',samples=len(frames),
        input=[original_width,original_height],active_crop=[x,y,width,height],
        final_quality_approved=False)
