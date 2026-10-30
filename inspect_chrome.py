from PIL import Image
p=r'C:\Users\BNN\Downloads\google-chrome-icon-logo-symbol-free-png.webp'
im=Image.open(p)
print(im.format, im.size, im.mode)
