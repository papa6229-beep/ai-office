from PIL import Image, ImageDraw
import math, random, os
src = r'C:\Users\BNN\.codex\generated_images\01a142d1-ba5f-7643-a9db-8e5f53a2c4e2\exec-1a4a9410-e243-4436-ab36-46c28b93b96d.png'
out = r'C:\Users\BNN\AI_OFFICE_GitHub\banana-mall-bounce-600x400.gif'
base = Image.open(src).convert('RGB').resize((600,400), Image.Resampling.LANCZOS)
frames=[]
N=24
for i in range(N):
    t=2*math.pi*i/N
    # Gentle squash-and-bounce; character remains fully in frame.
    scale=1+0.012*math.sin(t)
    w,h=round(600*scale),round(400*scale)
    art=base.resize((w,h),Image.Resampling.LANCZOS)
    canvas=Image.new('RGB',(600,400),(9,105,205))
    x=(600-w)//2
    y=(400-h)//2+round(5*math.sin(t))
    canvas.paste(art,(x,y))
    d=ImageDraw.Draw(canvas,'RGBA')
    # Floating pink petals around the mascot, cycling continuously.
    for j in range(7):
        phase=(i/N + j/7)%1
        px=70 + j*74 + 13*math.sin(t+j*1.7)
        py=390 - phase*390
        px += 16*math.sin(phase*2*math.pi+j)
        r=3+(j%3)
        col=(255,115,177,210)
        d.ellipse((px-r,py-r,px+r,py+r),fill=col)
        d.ellipse((px-r*0.4,py-r*1.8,px+r*0.4,py-r*0.2),fill=col)
    # Tiny glints pulse at alternating corners.
    for j,(sx,sy) in enumerate([(105,102),(492,126),(92,288),(504,300)]):
        q=max(0,math.sin(t*2+j*1.5))
        if q>0.30:
            rr=2+int(q*3)
            d.line((sx-rr*2,sy,sx+rr*2,sy),fill=(255,255,255,230),width=2)
            d.line((sx,sy-rr*2,sx,sy+rr*2),fill=(255,255,255,230),width=2)
    frames.append(canvas.convert('P',palette=Image.Palette.ADAPTIVE,colors=96))
frames[0].save(out,save_all=True,append_images=frames[1:],duration=85,loop=0,optimize=True,disposal=2)
print(out, os.path.getsize(out), Image.open(out).size, N)
