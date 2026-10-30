from PIL import Image
import os
p=r'C:\Users\BNN\Downloads\google-chrome-icon-logo-symbol-free-png.webp'
im=Image.open(p).convert('RGBA')
a=im.getchannel('A')
print('alpha bbox',a.getbbox(),'corners',[im.getpixel(pt) for pt in [(0,0),(979,0),(0,979),(979,979),(490,0)]])
