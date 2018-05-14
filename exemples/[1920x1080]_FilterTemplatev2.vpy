#FichierSource="test.mp4"
FichierSource = FichierSource.decode("utf-8") # [Arg] Fichier vidéo source
dir = dir.decode("utf-8") # [Arg] Dossier où placer les résultats produits (sous-images)
DimensionCropBox=[1344,150] # Taille en largeur et hauteur de la CropBox délimitant les sous-titres à OCR.
HauteurCropBox=46 # Hauteur de la CropBox délimitant les sous-titres à OCR.
HauteurCropBoxAlt=860 # Hauteur de la CropBox Alternative, utile pour l'OCR des indications. Double le temps de traitement. Mettre à -1 pour désactiver.
Supersampling=-1 # Facteur de supersampling (multiplication de la résolution de la vidéo). Mettre à -1 pour calculer le facteur automatiquement.
ExpandRatio=1 # EXPERIMENTAL ! Facteur Expand/Inpand. La valeur 1 est adaptée pour un Supersampling automatique (1080p). Calcul typique de la valeur : ExpandRatio="RésolutionFinale"/1080.
ModeU='sinc' # 'sinc' (2 taps, plus rapide), 'nnedi3' (plus lent) ou 'waifu2x' (beaucoup plus lent), contrôle la méthode d'Upscale.
SeuilI=230 # Seuil délimitant les sous-titres. Cette valeur correspond à la luminosité minimale de l'intérieur (Inline).
SeuilO=80 # Seuil délimitant les sous-titres. Cette valeur correspond à la luminosité maximale de l'extérieur (Outline).
SeuilSCD=0.03 # Un seuil trop bas augmente le nombre de faux positifs, un seuil trop haut ne permet pas de détecter tous les sous-titres.


import vapoursynth as vs
if ModeU=='nnedi3':
    import edi_rpow2 as edi
import havsfunc as haf
import functools
import numpy as np
import cv2 as cv
import mvsfunc as mvs
import sys
import os
from multiprocessing.dummy import Pool as ThreadPool 
core=vs.get_core()

def minimum(x,y):
    return min(x,y)


displayname = os.path.basename(FichierSource)

if not os.path.exists(os.path.join(dir, displayname)):
    os.makedirs(os.path.abspath(os.path.join(dir, displayname)))
    os.makedirs(os.path.abspath(os.path.join(dir, displayname, "default")))
    if HauteurCropBoxAlt != -1:
        os.makedirs(os.path.abspath(os.path.join(dir, displayname, "alt"))) 
    
Clip=core.ffms2.Source(source=FichierSource)
if type(SeuilI) is int and type(SeuilO) is int:
    Clip=core.std.ShufflePlanes(clips=Clip,planes=0,colorfamily=vs.GRAY)

if Supersampling<0:
    if Clip.width/Clip.height>16/9:    
        TargetRes=1920
        CurrentRes=Clip.width    
    else:
        TargetRes=1080
        CurrentRes=Clip.height
    if ModeU=='nnedi3':
        Ss=TargetRes/CurrentRes/1.125    
    else:
        Ss=TargetRes/CurrentRes
elif Supersampling==0:
    Ss=1
else:
    Ss=Supersampling

if ModeU=='nnedi3' and Ss!=1:
    if Ss-int(Ss)>0:
        Ss=int(Ss/2)*2+2
    else:
        Ss=int(Ss/2)*2
    if Supersampling<0:
        Ssbis=TargetRes/(CurrentRes*Ss)
    else:
        Ssbis=Supersampling/Ss

def Resizing(clip,largeur,hauteur,hauteur2):
    clip=core.std.CropAbs(clip=clip,width=largeur,height=hauteur,left=int((clip.width-largeur)/2),top=clip.height-hauteur2)
    if Ss!=1:
        if ModeU=='nnedi3' or ModeU=='waifu2x':
            if ModeU=='nnedi3':
                clip=edi.nnedi3_rpow2(clip=clip,rfactor=Ss)
            else :
                clip=core.fmtc.bitdepth(clip=clip,bits=32)
                clip=core.w2xc.Waifu2x(clip=clip,scale=Ss)
                if Ssbis!=1:
                    clip=core.fmtc.bitdepth(clip=clip,bits=16)
                else :
                    clip=core.fmtc.bitdepth(clip=clip,bits=8)
            if Ssbis!=1:
                clip=core.fmtc.resample(clip=clip,scale=Ssbis,kernel="sinc",taps=2)
                clip=core.fmtc.bitdepth(clip=clip,bits=8)
        else:
            clip=core.fmtc.resample(clip=clip,scale=Ss,kernel="sinc",taps=2)
            clip=core.fmtc.bitdepth(clip=clip,bits=8)
    elif clip.format.bits_per_sample!=8:
        clip=core.fmtc.bitdepth(clip=clip,bits=8)
    return clip
    
def RGBBinarize(clip,seuil):
    R=core.std.ShufflePlanes(clips=clip,planes=0,colorfamily=vs.GRAY)
    G=core.std.ShufflePlanes(clips=clip,planes=1,colorfamily=vs.GRAY)
    B=core.std.ShufflePlanes(clips=clip,planes=2,colorfamily=vs.GRAY)
    for i in range(0,int(len(seuil)/3)):
        i=i*3
        RGB=core.std.Expr(clips=[R,G,B],expr=["x "+str(seuil[i])+" >= y "+str(seuil[i+1])+" >= or z "+str(seuil[i+2])+" >= or 255 0 ?"])
        if i==0:
            clipfin=RGB
        else:
            clipfin=core.std.Merge(clipfin,RGB)
    clipfin=core.std.Binarize(clip=clipfin,threshold=1)
    return clipfin

def Cleaning(clip,blank,e):
    if type(SeuilI) is list or type(SeuilO) is list:
        clipRGB=core.fmtc.resample(clip=clip,css="444")
        clipRGB=core.fmtc.matrix(clip=clipRGB,mat="709",col_fam=vs.RGB)
        clipRGB=core.fmtc.bitdepth(clip=clipRGB,bits=8)
        
    if type(SeuilI) is int and type(SeuilO) is int:
        White_Raw=core.std.Binarize(clip=clip,threshold=SeuilI)
        Bright_Raw=core.std.Binarize(clip=clip,threshold=SeuilO)
    elif type(SeuilI) is int and type(SeuilO) is list:
        White_Raw=core.std.ShufflePlanes(clips=clip,planes=0,colorfamily=vs.GRAY)
        White_Raw=core.std.Binarize(clip=White_Raw,threshold=SeuilI)
        Bright_Raw=RGBBinarize(clipRGB,SeuilO)
    elif type(SeuilI) is list and type(SeuilO) is int:
        White_Raw=RGBBinarize(clipRGB,SeuilI)
        Bright_Raw=core.std.ShufflePlanes(clips=clip,planes=0,colorfamily=vs.GRAY)
        Bright_Raw=core.std.Binarize(clip=Bright_Raw,threshold=SeuilO)
    else:
        White_Raw=RGBBinarize(clipRGB,SeuilI)
        Bright_Raw=RGBBinarize(clipRGB,SeuilO)

    Bright_Out=core.std.Lut2(clipa=Bright_Raw,clipb=Rect,function=minimum)

    Bright_Not=core.misc.Hysteresis(clipa=Bright_Out,clipb=Bright_Raw)
    Bright_Not=core.std.Invert(Bright_Not)

    White_Txt=core.std.MaskedMerge(blank,White_Raw,Bright_Not)

    White_Lb=haf.mt_inpand_multi(src=White_Txt,sw=int(e),sh=int(e),mode="ellipse")
    White_Lb=haf.mt_expand_multi(src=White_Lb,sw=int(e),sh=int(e),mode="ellipse")

    White_Ub=haf.mt_inpand_multi(src=White_Txt,sw=int(5*e),sh=int(5*e),mode="ellipse")
    White_Ub=haf.mt_expand_multi(src=White_Ub,sw=int(3*e),sh=int(3*e),mode="ellipse")
    White_Ub=core.std.Invert(White_Ub)

    White=core.std.MaskedMerge(Blank,White_Lb,White_Ub)
    White=core.misc.Hysteresis(clipa=White,clipb=White_Txt)

    ClipCleaning=core.std.MaskedMerge(Blank,White_Raw,White)
    ClipCleaning=core.std.Median(clip=ClipCleaning)
    
    return ClipCleaning
    
def SceneLog(n,f,clip,dir,zero_pad):
    if (f[0].props._SceneChangePrev == 1 or f[0].props._SceneChangeNext == 1) and f[0].props.PlaneStatsMax > 1.:
        # sys.stderr.write(str(f[0].props.PlaneStatsMax))
        # open(log, "a").write(str(n)+" "+str(f.props._SceneChangePrev)+" "+str(f.props._SceneChangeNext)+"\n")
        nfn = ""
        if f[0].props._SceneChangePrev == 1:
            frame = f[1]
            v = cv.merge([np.array(frame.get_read_array(i), copy=False) for i in reversed(range(frame.format.num_planes))])
            nfn = "%0*d.png"  % (zero_pad, n)
            cv.imwrite(os.path.join(dir, nfn), v)
        with open(os.path.join(dir, "SceneChanges.csv"), "a") as ofile:
            ofile.write("%0*d,%d,%d,\"%s\"\n" % (zero_pad, n, f[0].props._SceneChangePrev, f[0].props._SceneChangeNext, nfn))
    return clip

HauteurCropBox=HauteurCropBox+DimensionCropBox[1]
if HauteurCropBoxAlt>=0:
    HauteurCropBoxAlt=HauteurCropBoxAlt+DimensionCropBox[1]

ClipResized=Resizing(Clip,DimensionCropBox[0],DimensionCropBox[1],HauteurCropBox)

Blackclip=core.std.BlankClip(width=int(ClipResized.width-20),height=int(ClipResized.height-20),format=vs.GRAY8,color=0)
Rect=core.std.AddBorders(clip=Blackclip,left=10,right=10,top=10,bottom=10,color=255)
Blank=core.std.BlankClip(ClipResized,format=vs.GRAY8)

ClipCleaned=Cleaning(ClipResized,Blank,ExpandRatio)
with open("%s/%s/default/SceneChanges.csv" % (dir, displayname), "w") as ofile:
    ofile.write("[Video Informations]\nfps=%f\nframe_count=%d\n\n[Scene Informations]\n" % (Clip.fps, Clip.num_frames))
    ofile.write("frame,is_start,is_end,subimage\n")
ClipCleanedSC=core.std.CropAbs(clip=ClipCleaned,width=int(ClipCleaned.width/2.7),height=int(ClipCleaned.height/2.7),left=int(ClipCleaned.width*(1-1/2.7)/2),top=int(ClipCleaned.height/2))
# ClipCleanedSC=ClipCleaned
ClipCleanedSC=core.misc.SCDetect(clip=ClipCleanedSC,threshold=SeuilSCD)

ClipCleanedSC = core.std.PlaneStats(ClipCleanedSC)

zero_pad = len(str(ClipCleaned.num_frames))
ClipCleaned=core.std.FrameEval(ClipCleaned,functools.partial(SceneLog,clip=ClipCleaned,dir=os.path.join(dir, displayname, "default"), zero_pad=zero_pad),prop_src=[ClipCleanedSC, ClipCleaned])


if HauteurCropBoxAlt>=0:
    ClipResizedAlt=Resizing(Clip,DimensionCropBox[0],DimensionCropBox[1],HauteurCropBoxAlt)
    
    ClipCleanedAlt=Cleaning(ClipResizedAlt,Blank,ExpandRatio)
    
    with open("%s/%s/alt/SceneChanges.csv" % (dir, displayname), "w") as ofile:
        ofile.write("[Video Informations]\nfps=%f\nframe_count=%d\n\n[Scene Informations]\n" % (Clip.fps, Clip.num_frames))
        ofile.write("frame,is_start,is_end,subimage\n")
    ClipCleanedAltSC=core.std.CropAbs(clip=ClipCleanedAlt,width=int(ClipCleanedAlt.width/2.7),height=int(ClipCleanedAlt.height/2.7),left=int(ClipCleanedAlt.width*(1-1/2.7)/2),top=int(ClipCleanedAlt.height*(1/2-1/2.7)))
    # ClipCleanedAltSC=ClipCleanedAlt
    ClipCleanedAltSC=core.misc.SCDetect(clip=ClipCleanedAltSC,threshold=SeuilSCD)
    ClipCleanedAltSC = core.std.PlaneStats(ClipCleanedAltSC)
    ClipCleanedAlt=core.std.FrameEval(ClipCleanedAlt,functools.partial(SceneLog,clip=ClipCleanedAlt,dir=os.path.join(dir, displayname, "alt"), zero_pad=zero_pad),prop_src=[ClipCleanedAltSC, ClipCleanedAlt])
    
    Clip=core.std.StackVertical([ClipCleanedAlt,ClipCleaned])

else:
    Clip=ClipCleaned
    
Clip.set_output()
