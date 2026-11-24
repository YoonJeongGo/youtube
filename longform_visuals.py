"""Independent longform picture edits on a measured audio timeline."""
from bisect import bisect_right

class VisualShotPlan:
    def __init__(self, shots, allowed_images, total_frames=None, fps=30):
        self.shots = list(shots or [])
        self.starts = []
        edge = 0
        previous_key = None
        hold_start = 0
        for i, shot in enumerate(self.shots):
            if not isinstance(shot, dict):
                raise ValueError('visual_shots entries must be objects')
            start, end = shot.get('start_frame'), shot.get('end_frame')
            if type(start) is not int or type(end) is not int or start != edge or end <= start:
                raise ValueError('visual_shots must cover contiguous positive frame intervals from zero')
            if shot.get('image') not in allowed_images:
                raise ValueError('visual_shots must use an episode-declared image')
            for field in ('crop', 'caption_box'):
                box = shot.get(field)
                if box is not None and (not isinstance(box, (list, tuple)) or len(box) != 4
                        or any(type(v) is not int for v in box)
                        or not (0 <= box[0] < box[2] <= 1920 and 0 <= box[1] < box[3] <= 1080)):
                    raise ValueError(field + ' must be a rectangle inside 1920x1080')
            if shot.get('transition', 'hardcut') not in ('hardcut', 'crossfade'):
                raise ValueError('visual shot transition must be hardcut or crossfade')
            key = self.key(shot)
            if key != previous_key:
                hold_start = start
            if end - hold_start > 20 * fps:
                raise ValueError('An unchanged picture composition exceeds 20 seconds')
            previous_key = key
            self.starts.append(start)
            edge = end
        if self.shots and total_frames is not None and edge != total_frames:
            raise ValueError('visual_shots do not cover the complete measured timeline')

    @staticmethod
    def key(shot):
        return (shot['image'], tuple(shot.get('crop') or ()))

    def at(self, frame):
        if not self.shots:
            return None, None
        i = bisect_right(self.starts, frame) - 1
        if i < 0 or frame >= self.shots[i]['end_frame']:
            raise ValueError('Frame lies outside the visual shot plan')
        return i, self.shots[i]

    def dissolve_frames(self, i, fps=30):
        shot = self.shots[i]
        if i == 0 or shot.get('transition', 'hardcut') != 'crossfade':
            return 0
        if self.key(self.shots[i-1]) == self.key(shot):
            return 0
        frames = round(.2 * fps)
        return frames if shot['end_frame'] - shot['start_frame'] >= frames else 0



def timeline_picture_shots(timeline):
    """Infer unchanged full-image holds when no crop plan has been supplied."""
    shots = []
    for cue in timeline["segments"]:
        if shots and shots[-1]["image"] == cue["image"]:
            shots[-1]["end_frame"] = cue["end_frame"]
        else:
            shots.append({"start_frame":cue["start_frame"],"end_frame":cue["end_frame"],
                          "image":cue["image"]})
    return shots
