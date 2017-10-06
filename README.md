A python program to OCR videos

It uses vapoursynth, ffmpeg and tesseract along with some python modules

Requirements
============

Install python 3+
Install vapoursynth
Install tesseract
(optionnal) install Vapoursynth Editor

have python, ffmpeg and vspipe (vapoursynth) in the path

install vapoursynth plugins

For windows:
1. unzip the content of dependecies/Win/vapoursynth_dep.zip to you vapoursynth plugin folder
2. unzip the content of dependecies/Win/python_dep.zip to you python site-package
    
For Linux:
- install [Vapoursynth-plugins](https://github.com/darealshinji/vapoursynth-plugins) (or use my containerized version of pythoCR)

Install (from pip) python prerequisites:

`$pip3 install colorama configargparse pyEnchant tqdm`

Installation
============

clone this repository

Here, you're done.

How to use
==========


```
$python3 pythOcr.py --help
usage: PythoCR [-h] [--version] [-c CONFIG] [-l language] [-wd folder]
               [-o folder] [--log-level level] [--ass-style style]
               [-rr path to regex-replace json] [-hcr char,replace]
               [--sub-format format] [--mode mode] [--vpy vpy_file]
               [--threads number] [--auto-same-sub-threshold number]
               [--same-sub-threshold number] [--no-spellcheck] [-t] [-d]
               path [path ...]

Process a previously filtered video and extract subtitles as srt. Args that
start with '--' (eg. --version) can also be set in a config file (specified
via -c). Config file syntax allows: key=value, flag=true, stuff=[a,b,c] (for
details, see syntax at https://goo.gl/R74nmi). If an arg is specified in more
than one place, then commandline values override config file values which
override defaults.

positional arguments:
  path                  path to a filtered video

optional arguments:
  -h, --help            show this help message and exit
  --version             show program's version number and exit
  -c CONFIG, --config CONFIG
                        path to configuration file
  -l language, --lang language
                        Select the language of the subtitles (default: fra)
  -wd folder, --work-dir folder
                        Directory where I will put all my temporary stuff
                        (default ./temp)
  -o folder, --output-dir folder
                        Directory where I will put all my released stuff
                        (default ./output)
  --log-level level     Set the logging level (default INFO)
  --ass-style style     Ass style to use if sub-format is ass (default:
                        Verdana 60)
  -rr path to regex-replace json, --regex-replace path to regex-replace json
                        List of regex/replace for automatic correction
  -hcr char,replace, --heuristic-char-replace char,replace
                        List of char/replace for heuristic correction
  --sub-format format   Set the outputed subtitles format (default srt)
  --mode mode           Set the processing mode. "filter" to only start the
                        filtering jobs, "ocr" to process already filtered
                        videos, "full" for both. (default full)
  --vpy vpy_file        vapoursynth file to use for filtering (required for
                        "filter only" and "full" modes
  --threads number      Number of threads the script will use (default:
                        automatic detection)
  --auto-same-sub-threshold number
                        Percentage of comparison to assert that two lines of
                        subtitles are automatically the same (default: 95%)
  --same-sub-threshold number
                        Percentage of comparison to assert that two lines of
                        subtitles are the same (default: 80%)
  --no-spellcheck       Desactivate the function which tries to replace
                        allegedly bad characters using spellcheck (it will
                        make the "heurist_char_replace" option of the
                        userconfig useless)
  -t, --timid           Activate timid mode (it will ask for user input when
                        some corrections are not automatically approved)
  -d, --delay           Delay correction after every video is processed
```

So to process the video /myVideos/vid01.mp4, the command would be `python3 pythoCR.py -c <myconfig> --vpy <myvpy> /myVideos/vid01.mp4`
