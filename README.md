# ev3-robonomics

A simple example of a robot as an economic agent.

Repeat process:
0. On https://polkadot.js.org/apps/?rpc=wss%3A%2F%2Fkusama.rpc.robonomics.network%2F#/explorer in accounts tab create 
one more account for the robot and transfer 0.1 XRT to it.
1. Clone https://github.com/PaTara43/ev3-robonomics
2. Clone https://github.com/PaTara43/ev3-robonomics-client
3. `pip3 install robonomics_interface`
4. `pip3 install paho-mqtt`
5. Install mosquitto with `sudo apt install -y mosquitto`. It will be added to the systemctl services, so you may want
to disable it later.
6. Edit lines 44-45 of `ev3-robonomics/agent.py` lines 33-34 of `ev3-robonomics-client/client.py` and lines 28-29 of 
`ev3-robonomics-client/ev3_simulator.py` with `127.0.0.1` address and `1883` port
7. Launch:
- In the 1st terminal:
```bash
export EV3_SEED=""  # Your robot 12-words seed phrase
python3 agent.py 
```
- In the 2nd terminal:
```bash
python3 ev3_simulator.py
```
- In the 3rd terminal:
```bash
export SEED=""  # Your user/client 12-words seed phrase
python3 client.py
# Hit enter once more after 2 secs.
```