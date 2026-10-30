from PIL import Image
import os
src=r'C:\Users\BNN\Downloads\Filezilla_30146.png'
out=r'C:\Users\BNN\AI_OFFICE_GitHub\filezilla-dark-600x400.png'
im=Image.open(src).convert('RGBA')
# Source has a black backdrop; replace only the near-black pixels with transparency.
pix=im.load()
for y in range(im.height):
    for x in range(im.width):
        r,g,b,a=pix[x,y]
        if r<8 and g<8 and b<8:
            pix[x,y]=(0,0,0,0)
# Fit the intact icon within 368x368 preserving its square proportions.
im.thumbnail((368,368),Image.Resampling.LANCZOS)
bg=Image.new('RGBA',(600,400),(30,33,39,255))
bg.alpha_composite(im,((600-im.width)//2,(400-im.height)//2))
bg.convert('RGB').save(out,optimize=True)
print(out, os.path.getsize(out))
