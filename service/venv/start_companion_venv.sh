#/usr/bin/env bash
cd /var/lib/meshroom_companion;
if [ ! -d "./venv" ];
then
  python -m venv venv;
  source ./venv/bin/activate;
  pip install -r ./venv_requirements.txt
else
  source ./venv/bin/activate;
  pip install -r ./venv_requirements.txt
fi
source ./venv/bin/activate;
python ./meshroom_companion.py
