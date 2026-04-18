#!/usr/bin/env python3
"""
skills-master visual — Dispatch Meridians (refined)
Signal-intelligence aesthetic. Museum-quality precision.
"""

from PIL import Image, ImageDraw, ImageFont
import math, os

W, H = 1400, 875
img = Image.new('RGB', (W, H), (6, 6, 14))
draw = ImageDraw.Draw(img)

# ── Palette ───────────────────────────────────────────────────────────────────
BG        = (6,   6,  14)
GOLD      = (196, 164, 72)
GOLD2     = (130, 104, 40)
GOLD3     = (50,  40,  14)
WHITE     = (228, 222, 210)
OFF       = (168, 162, 150)
DIM       = (68,  66,  84)
DIM2      = (20,  20,  34)
RED       = (140,  50,  50)
RED_L     = (220, 110, 110)
GREEN     = ( 46, 110,  58)
GREEN_L   = (110, 200, 130)
BLUE      = ( 50, 120, 160)
BLUE_L    = ( 90, 170, 210)

# ── Fonts ─────────────────────────────────────────────────────────────────────
FD = '/Users/airbook/.claude/skills/canvas-design/canvas-fonts/'
def f(n, s): return ImageFont.truetype(FD + n, s)

F_TITLE  = f('BricolageGrotesque-Bold.ttf',    60)
F_TAG    = f('BricolageGrotesque-Regular.ttf',  16)
F_STAT   = f('Outfit-Bold.ttf',                38)
F_STAT_L = f('Outfit-Regular.ttf',             12)
F_CARD_H = f('Outfit-Bold.ttf',                15)
F_CARD_S = f('Outfit-Regular.ttf',             11)
F_MONO   = f('JetBrainsMono-Regular.ttf',       10)
F_MONO_B = f('JetBrainsMono-Bold.ttf',          10)
F_MONO_S = f('JetBrainsMono-Regular.ttf',        8)
F_MONO_T = f('JetBrainsMono-Regular.ttf',        9)
F_Q      = f('JetBrainsMono-Bold.ttf',          13)

# ── Helpers ───────────────────────────────────────────────────────────────────
def hex_pts(cx, cy, r, flat=False):
    return [(cx + r*math.cos(math.radians(60*i+(0 if flat else 30))),
             cy + r*math.sin(math.radians(60*i+(0 if flat else 30)))) for i in range(6)]

def ctext(x, y, t, fn, col):
    bb = draw.textbbox((0,0), t, font=fn)
    draw.text((x-(bb[2]-bb[0])//2, y-(bb[3]-bb[1])//2), t, font=fn, fill=col)

def ln(x1,y1,x2,y2,c,w=1):
    draw.line([(x1,y1),(x2,y2)], fill=c, width=w)

def dot(x,y,r,c):
    draw.ellipse([(x-r,y-r),(x+r,y+r)], fill=c)

def rect(x,y,w,h,fill=None,outline=None):
    draw.rectangle([(x,y),(x+w,y+h)], fill=fill, outline=outline)

# ── Background grid ───────────────────────────────────────────────────────────
for gx in range(0, W+40, 36):
    ln(gx,0,gx,H,(11,11,20))
for gy in range(0, H+40, 36):
    ln(0,gy,W,gy,(11,11,20))

# ── Corner marks ──────────────────────────────────────────────────────────────
M = 28
for (cx,cy,dx,dy) in [(M,M,1,1),(W-M,M,-1,1),(M,H-M,1,-1),(W-M,H-M,-1,-1)]:
    ln(cx,cy,cx+dx*18,cy,DIM); ln(cx,cy,cx,cy+dy*18,DIM); dot(cx,cy,2,DIM)

# ── Divider ───────────────────────────────────────────────────────────────────
DVX = 408
ln(DVX,36,DVX,H-36,(18,18,32)); dot(DVX,36,3,(26,26,40)); dot(DVX,H-36,3,(26,26,40))

# ═══════════════════════════════════════════════════════════════════════════════
# LEFT PANEL (0..407)
# ═══════════════════════════════════════════════════════════════════════════════
LX = 48

# Title
draw.text((LX, 56), 'skills-master', font=F_TITLE, fill=WHITE)

# Taglines
draw.text((LX, 126), '3-question routing engine', font=F_TAG, fill=GOLD2)
draw.text((LX, 146), 'for 2,700+ Claude Code skills', font=F_TAG, fill=DIM)

ln(LX, 170, LX+316, 170, GOLD3)

# Problem
draw.text((LX, 182), 'PROBLEM', font=F_MONO_S, fill=DIM)
for i, t in enumerate([
    'Claude picks the wrong skill 20–30% of',
    'the time. It rationalizes skipping skills,',
    'burns opus tokens on haiku tasks, fires',
    'brainstorming when you need debugging.'
]):
    draw.text((LX, 196+i*14), t, font=F_MONO_S, fill=OFF)

ln(LX, 262, LX+316, 262, GOLD3)

# Fix
draw.text((LX, 270), 'FIX', font=F_MONO_S, fill=DIM)
for i, t in enumerate([
    'Three questions. Always the right',
    'skill + agent + model. First time.'
]):
    draw.text((LX, 284+i*15), t, font=F_MONO, fill=GOLD)

ln(LX, 326, LX+316, 326, GOLD3)

# Q chips
draw.text((LX, 334), 'THE THREE QUESTIONS', font=F_MONO_S, fill=DIM)

chips = [
    ('Q1', 'BROKEN?',  'error · crash · test fail',    RED,   RED_L),
    ('Q2', 'BUILD?',   'new feature · page · script',  GREEN, GREEN_L),
    ('Q3', 'OPERATE?', 'ship · refactor · configure',  BLUE,  BLUE_L),
]
qy = 350
for q, lbl, sub, bg, fg in chips:
    rect(LX, qy, 24, 22, fill=bg)
    ctext(LX+12, qy+11, q, F_MONO_B, WHITE)
    draw.text((LX+30, qy+2), lbl, font=F_MONO_B, fill=fg)
    draw.text((LX+30, qy+14), sub, font=F_MONO_S, fill=DIM)
    qy += 36

# Discovery callout
DY_BOX = qy + 8
rect(LX-2, DY_BOX, 322, 76, fill=(8,8,18), outline=GOLD3)
draw.text((LX+6, DY_BOX+7), 'DISCOVERY PROTOCOL', font=F_MONO_S, fill=GOLD2)
draw.text((LX+6, DY_BOX+20), 'No match in the table?', font=F_MONO, fill=OFF)
for i, t in enumerate([
    '→ Antigravity (860+ skills)',
    '→ Composio (944+ integrations)',
    '→ GitHub SKILL.md repos  ← key differentiator',
    '→ auto-clone · auto-install',
]):
    col = GOLD if i >= 2 else DIM
    draw.text((LX+6, DY_BOX+34+i*11), t, font=F_MONO_S, fill=col)

# Stats
sy = H - 110
stats = [('2,700+','skills'), ('90%','accuracy'), ('10s','per route')]
sx = LX
for val, lbl in stats:
    draw.text((sx, sy), val, font=F_STAT, fill=WHITE)
    draw.text((sx, sy+44), lbl, font=F_STAT_L, fill=DIM)
    bb = draw.textbbox((0,0), val, font=F_STAT)
    sx += max(bb[2]-bb[0], 90) + 18

draw.text((LX, H-28), 'github.com/hussi9/skills-master', font=F_MONO_S, fill=DIM)

# ═══════════════════════════════════════════════════════════════════════════════
# RIGHT PANEL — Routing Diagram (408..1400)
# ═══════════════════════════════════════════════════════════════════════════════
CX, CY = 865, 420

# Ambient glow (radial fade)
for ri in range(260, 30, -22):
    v = max(4, int(20*(1-ri/260)))
    draw.ellipse([(CX-int(ri*1.5), CY-ri), (CX+int(ri*1.5), CY+ri)], fill=(v, v, v+6))

# Background spokes
for a in range(0, 360, 20):
    ex = CX + 300*math.cos(math.radians(a))
    ey = CY + 230*math.sin(math.radians(a))
    ln(CX, CY, int(ex), int(ey), (14,14,24))

# ── Central hex ──────────────────────────────────────────────────────────────
for ri, col in [(104,(16,16,28)), (88,(18,18,30)), (72,DIM2)]:
    draw.polygon(hex_pts(CX,CY,ri,True), outline=col, fill=None)
draw.polygon(hex_pts(CX,CY,58,True), outline=GOLD2, fill=(12,12,26))
draw.polygon(hex_pts(CX,CY,42,True), outline=GOLD, fill=(16,16,36))

ctext(CX, CY-9, 'ROUTE', F_MONO_B, GOLD)
ctext(CX, CY+9, 'ENGINE', F_MONO_S, GOLD2)

# ── Branches ─────────────────────────────────────────────────────────────────
BW, BH = 238, 118

branches = [
    dict(q='Q1', label='BROKEN?',  sub='error · crash · test fail · wrong output',
         skill='systematic-debugging',   model='sonnet  (→ opus in prod)',
         nx=575, ny=192, bg=RED, fg=RED_L),
    dict(q='Q2', label='BUILD?',   sub='new feature · page · script · integration',
         skill='brainstorming → domain',  model='sonnet  (opus for auth)',
         nx=1240, ny=192, bg=GREEN, fg=GREEN_L),
    dict(q='Q3', label='OPERATE',  sub='ship · refactor · review · deploy · docs',
         skill='verification → deploy',   model='sonnet',
         nx=1240, ny=648, bg=BLUE, fg=BLUE_L),
]

def branch(b):
    nx, ny = b['nx'], b['ny']
    bg, fg = b['bg'], b['fg']

    # Direction vector from center
    dx, dy = nx - CX, ny - CY
    dist = math.sqrt(dx*dx+dy*dy)
    # Start at hex edge
    ratio = 64/dist
    sx = int(CX + dx*ratio)
    sy = int(CY + dy*ratio)

    # Determine elbow: horizontal first, then vertical
    if nx < CX:
        # Left branch: go left then up
        ex = sx + int((nx-sx)*0.5)
        ey = sy
        ln(sx,sy,ex,ey,GOLD2,2);   dot(ex,ey,3,GOLD)
        ln(ex,ey,ex,ny,GOLD2,2);   dot(ex,ny,3,GOLD)
        attach_x = nx+BW//2-30
        ln(ex,ny,attach_x,ny,GOLD2,2); dot(attach_x,ny,5,fg)
    else:
        # Right branches: go right then up/down
        ex = sx + int((nx-sx)*0.42)
        ey = sy
        ln(sx,sy,ex,ey,GOLD2,2);   dot(ex,ey,3,GOLD)
        ln(ex,ey,ex,ny,GOLD2,2);   dot(ex,ny,3,GOLD)
        attach_x = nx-BW//2+30
        ln(ex,ny,attach_x,ny,GOLD2,2); dot(attach_x,ny,5,fg)

    # Box shadow
    rect(nx-BW//2+2, ny-BH//2+2, BW, BH, fill=(bg[0]//6,bg[1]//6,bg[2]//6))
    # Box
    rect(nx-BW//2, ny-BH//2, BW, BH, fill=(8,8,18), outline=bg)

    # Q-chip strip (left side)
    CW = 28
    rect(nx-BW//2, ny-BH//2, CW, BH, fill=bg)
    ctext(nx-BW//2+CW//2, ny, b['q'], F_Q, WHITE)

    # Content
    ix = nx-BW//2+CW+9
    iy = ny-BH//2+10
    draw.text((ix,iy), b['label'], font=F_CARD_H, fill=fg)
    iy += 19
    draw.text((ix,iy), b['sub'], font=F_MONO_S, fill=DIM)
    iy += 13
    ln(ix, iy, nx+BW//2-8, iy, GOLD3)
    iy += 8
    draw.text((ix,iy), '→ '+b['skill'], font=F_MONO_T, fill=GOLD)
    iy += 15
    draw.text((ix,iy), 'model: '+b['model'], font=F_MONO_S, fill=DIM)
    iy += 12
    draw.text((ix,iy), 'agent: auto-selected', font=F_MONO_S, fill=DIM)

for b in branches:
    branch(b)

# ── Completion gate (center bottom) ──────────────────────────────────────────
GBW, GBH = 420, 56
GX, GY = CX - GBW//2, 790
ln(CX, CY+68, CX, GY, GOLD3); dot(CX,GY,3,GOLD3)

rect(GX, GY, GBW, GBH, fill=(8,8,18), outline=GOLD3)
ctext(CX, GY+10, 'NO MATCH? → DISCOVERY PROTOCOL', F_MONO_S, DIM)
ctext(CX, GY+24, 'Antigravity · Composio · GitHub SKILL.md repos', F_MONO_T, OFF)
ctext(CX, GY+38, 'auto-clone · auto-install · or generate a new skill', F_MONO_S, GOLD)
dot(CX, GY, 4, GOLD3)

# ── Top-right label ───────────────────────────────────────────────────────────
draw.text((W-226, H-24), 'one file · zero config · 90% routing accuracy', font=F_MONO_S, fill=DIM)
draw.text((W-158, 26),   'signal routing protocol', font=F_MONO_S, fill=DIM)

# ── Save ──────────────────────────────────────────────────────────────────────
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'skills-master-visual.png')
img.save(out, 'PNG', dpi=(144,144))
print(f'Saved: {out}  [{W}×{H}]')
