// Encode only already-validated artwork supplied by the local Divoom transport.
import {createRequire} from 'node:module';
import path from 'node:path';
const require=createRequire(import.meta.url);
const sharp=require(path.join(process.argv[2],'sharp'));
let input=[];let size=0;
for await(const chunk of process.stdin){size+=chunk.length;if(size>12*1024*1024)throw Error('Artwork input too large');input.push(chunk);}
const value=JSON.parse(Buffer.concat(input).toString('utf8'));
if(!Array.isArray(value.members)||value.members.length<1||value.members.length>5)throw Error('Invalid member count');
const count=value.compact===true?10:20,step=value.compact===true?300:150,bytes=128*128*4,stack=Buffer.alloc(bytes*count*value.members.length);
for(let n=0;n<value.members.length;n++){
 const source=Buffer.from(value.members[n],'base64');
 const meta=await sharp(source,{animated:true}).metadata();
 if(meta.width!==128||meta.pageHeight!==128)throw Error('Unexpected approved artwork dimensions');
 const raw=await sharp(source,{animated:true}).flatten({background:'#000'}).ensureAlpha().raw().toBuffer();
 const ends=[];let total=0;for(const delay of meta.delay){total+=delay;ends.push(total);}
 for(let i=0;i<count;i++){
  const time=(i*step)%total,frame=ends.findIndex(x=>x>time);
  raw.copy(stack,(n*count+i)*bytes,frame*bytes,(frame+1)*bytes);
 }
}
const result=await sharp(stack,{raw:{width:128,height:128*count*value.members.length,channels:4,pageHeight:128}})
 .gif({loop:0,delay:Array(count*value.members.length).fill(step),colours:128,dither:0,effort:4}).toBuffer();
if(result.length>4*1024*1024)throw Error('Encoded artwork too large');
process.stdout.write(result);
