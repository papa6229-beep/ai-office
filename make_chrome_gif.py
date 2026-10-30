from PIL import Image, ImageDraw
import os, math
src=r'C:\Users\BNN\Downloads\google-chrome-icon-logo-symbol-free-png.webp'
out=r'C:\Users\BNN\AI_OFFICE_GitHub\chrome-orbit-600x400.gif'
im=Image.open(src).convert('RGBA')
im.thumbnail((360,360),Image.Resampling.LANCZOS)
frames=[]
N=24
for i in range(N):
    # The Chrome color segments orbit while the familiar circular icon stays centered.
    rot=im.rotate(i*360/N,resample=Image.Resampling.BICUBIC,expand=False)
    canvas=Image.new('RGBA',(600,400),(27,30,36,255))
    canvas.alpha_composite(rot,((600-rot.width)//2,(400-rot.height)//2))
    frames.append(canvas.convert('RGB').quantize(colors=96,method=Image.Quantize.MEDIANCUT))
frames[0].save(out,save_all=True,append_images=frames[1:],duration=85,loop=0,optimize=True,disposal=2)
print(out,os.path.getsize(out),Image.open(out).size,N)
# Save a still preview of the first frame for visual confirmation.
frames[0].convert('RGB').save(r'C:\Users\BNN\AI_OFFICE_GitHub\chrome-preview.jpg',quality=90)
