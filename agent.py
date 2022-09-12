"""
This is a sample agent script to provide possibility to turn EV3 Robot in an economic agent.

"""

import logging
import time
import traceback
import typing as tp

from ast import literal_eval
from os import getenv
from paho.mqtt.client import Client
from robonomicsinterface import (
    Account,
    ipfs_get_content,
    ipfs_upload_content,
    ipfs_32_bytes_to_qm_hash,
    Liability,
    Subscriber,
    SubEvent,
)
from threading import Thread

logger = logging.getLogger(__name__)


class EV3:
    """
    EV3 Robot class.

    """

    def __init__(self):
        self.status: int = 0  # 0 - free, 1 - got an offer, waiting for liability, 2 - executing liability.
        self.pending_address: tp.Optional[str] = None

        self.seed: str = getenv("EV3_SEED")
        self.ev3_acc: Account = Account(seed=self.seed)

        self.mqtt_broker: str = "127.0.0.1"
        self.mqtt_port: int = 1893
        self.mqtt_client_id: str = "ev3_agent"
        self.mqtt_topics: list = [("offer", 0), ("response", 0), ("ev3_task", 0), ("ev3_report", 0)]
        self.mqtt_client: Client = self.connect_to_mqtt()

        self.report: tp.Optional[dict] = None

    def callback_new_liability(self, data):
        """
        Process new Liability to find if promisor is the agent, execute task then.

        :param data: New liability data: index, hash, price, promisee, promisor

        """

        if data[4] == self.ev3_acc.get_address():

            try:
                if self.status != 0:
                    raise Exception("Robot busy. Ignoring new liability.")
                if data[3] != self.pending_address:
                    raise Exception(f"Not waiting for a liability from this address {data[3]}.")

                logger.info(f"New liability for the EV3: {data}")

                self.status = 2

                cid: str = ipfs_32_bytes_to_qm_hash(data[1]["hash"])
                technics: tp.Dict[str, tp.Dict[int, int]] = ipfs_get_content(cid)

                self.publish_mqtt(self.mqtt_topics[2][0], str(technics))

                while True:
                    time.sleep(2)
                    if self.report:
                        logger.info("Got report from the EV3. Finalizing liability.")
                        break

                liability_report_tr_hash: str = self.report_liability(index=data[0], report_content=self.report)
                self.report = None
                logger.info(f"Reported liability {data[0]} at {liability_report_tr_hash}")

            except Exception:
                logger.error(f"Failed to process new liability: {traceback.format_exc()}")

            finally:
                self.status = 0

    def report_liability(self, index: int, report_content: dict) -> str:
        """
        Report liability with a EV3 sensor logs.

        :param index: liability index.
        :param report_content: Sensor logs.

        :return: Liability finalization transaction hash.

        """

        report_hash = ipfs_upload_content(self.seed, report_content)

        liability_manager = Liability(self.ev3_acc)

        return liability_manager.finalize(index=index, report_hash=report_hash)

    def subscribe_new_liability(self):
        """
        Subscribe to incoming liabilities to process them.

        """

        logger.info("Starting liability subscriber... Waiting for incoming liabilities.")
        Subscriber(
            account=self.ev3_acc,
            subscribed_event=SubEvent.NewLiability,
            subscription_handler=self.callback_new_liability,
        )

    def connect_to_mqtt(self) -> Client:
        """
        Connect to a MQTT broker.

        :return: MQTT client instance.

        """

        def on_connect(client, userdata, flags, rc):
            if rc == 0:
                logger.info("Connected to MQTT Broker!")
            else:
                logger.error("Failed to connect, return code %d\n", rc)
                raise Exception

        # Set Connecting Client ID
        client: Client = Client(self.mqtt_client_id)
        client.on_connect = on_connect
        client.connect(self.mqtt_broker, self.mqtt_port)
        return client

    def publish_mqtt(self, topic: str, message: str):
        """
        Publish a message to MQTT topic.

        :param topic: MQTT topic
        :param message: Message to send.

        """

        result = self.mqtt_client.publish(topic, message)
        status = result[0]
        if status == 0:
            logger.info(f"Sent `{message}` to topic `{topic}`.")
        else:
            logger.error(f"Failed to send message to topic {topic}")

    def on_offer(self, message: str):
        """
        Scenario to play of user has sent an offer.

        :param message: User offer message.
        """

        try:

            logger.info("New offer!")
            message_dict: dict = literal_eval(message)
            address, route, price = message_dict[0], message_dict[1], message_dict[2]

        except Exception:
            logger.error(f"Failed to parse offer {traceback.format_exc()}.")
            return

        try:

            valid_motor_value: bool = self.check_motor_values(route)
            valid_time_value: bool = self.check_time_values(route)
            work_cost: int = self.calculate_work_cost(route)
            logger.info(f"Offer price: {price}, work cost: {work_cost}.")

            if not valid_motor_value:
                logger.info("Invalid motor value in task. Motor values must be floats or ints [-100, 100].")
                self.publish_mqtt(
                    self.mqtt_topics[1][0],
                    str(
                        dict(
                            addr=address,
                            res=0,
                            log=f"Invalid motor value in task. Motor values must be floats or ints [-100, 100].",
                        )
                    ),
                )
            elif not valid_time_value:
                logger.info("Invalid time value. Time values must be positive and not exceed 5 minutes in total.")
                self.publish_mqtt(
                    self.mqtt_topics[1][0],
                    str(
                        dict(
                            addr=address,
                            res=0,
                            log=f"Invalid time value. Time values must be positive and not exceed 5 minutes in total.",
                        )
                    ),
                )
            elif work_cost >= price:
                logger.info("Too small price.")
                self.publish_mqtt(
                    self.mqtt_topics[1][0],
                    str(dict(addr=address, res=0, log=f"Too small price. Minimum price is {work_cost}.")),
                )
            elif self.status != 0:
                logger.info("Robot busy.")
                self.publish_mqtt(
                    self.mqtt_topics[1][0],
                    str(dict(addr=address, res=0, log="Robot busy.")),
                )
            else:
                logger.info("Offer accepted.")
                self.status = 1
                self.accept_offer_procedure(address, route, price)

        except Exception:
            logger.error(f"Error while processing offer: {traceback.format_exc()}")
            self.publish_mqtt(self.mqtt_topics[1][0], str(dict(addr=address, res=0, log="Failed to process query.")))
            self.status = 0
            self.pending_address = None

    def on_report(self, message: str):
        """
        Scenario to play if EV3 has sent a task report.

        :param message: EV3 report message.

        """

        self.report: dict = literal_eval(message)

    def on_message(self, client, userdata, msg):
        """
        What to do on income MQTT message.

        :param client: MQTT client.
        :param userdata: MQTT userdata.
        :param msg: Income message.
        """
        logger.info(f"Received `{msg.payload.decode()}` from `{msg.topic}` topic")
        if msg.topic == self.mqtt_topics[0][0]:
            self.on_offer(msg.payload.decode())
        elif msg.topic == self.mqtt_topics[3][0]:
            self.on_report(msg.payload.decode())

    def subscribe_mqtt(self):
        """
        Subscribe to a 'negotiations' topic on a Mosquitto broker.

        """

        self.mqtt_client.subscribe(self.mqtt_topics)
        self.mqtt_client.on_message = self.on_message

        logger.info("Started MQTT subscriber.")

    @staticmethod
    def calculate_work_cost(route: list) -> int:
        """
        Calculate robot work cost in XRT decimals.

        :param route: Route list.

        :return: Work cost in weiners.

        """

        agg_work: int = 0
        for stages in route:
            agg_work += (stages[0] + stages[1]) * stages[2]

        work_cost: int = (agg_work / 100 + 1) * 10**7  # 0.01 XRT for each 100 units of work

        return work_cost

    @staticmethod
    def check_motor_values(route: list) -> bool:
        """
        Check motor values to be valid.

        :param route: Route list.

        :return: True if valid, else false

        """

        motor_values = [sublist[:2] for sublist in route]
        for i in motor_values:
            for j in i:
                if type(j) != int and type(j) != float:
                    return False
                if j > 100 or j < -100:
                    return False
        return True

    @staticmethod
    def check_time_values(route: list) -> bool:
        """
        Check time to be valid.

        :param route: Route list.

        :return: True if valid, else false

        """

        time_values = [sublist[2:] for sublist in route]
        for i in time_values:
            if type(i) != int and type(i) != float:
                return False
            if i < 0:
                return False
        return True

    def accept_offer_procedure(self, address: str, route: list, price: int):
        """
        Send liability specs to the user for him to create a liability, set timeout. As soon as the offer is accepted,
            the agent is waiting for the liability from the user for 5 mins. After that, all the liabilities will be
            ignored and robot status will switch to 0 - Free.

        :param address: User address.
        :param route: Ordered route.
        :param price: Offered price.


        """

        try:
            technics: str = ipfs_upload_content(self.seed, route)
            liability_signer: Liability = Liability(self.ev3_acc)
            signature: str = liability_signer.sign_liability(technics, price)

            self.publish_mqtt(
                self.mqtt_topics[1][0],
                str(
                    dict(
                        addr=address,
                        res=1,
                        technics=technics,
                        price=price,
                        ev3_addr=self.ev3_acc.get_address(),
                        signature=signature,
                    )
                ),
            )
            self.pending_address = address

            time.sleep(5 * 60)
            self.pending_address = None
            if self.status == 1:
                self.status = 0

        except Exception:
            logger.error(f"Failed to send liability specs: {traceback.format_exc()}")
            self.publish_mqtt(
                self.mqtt_topics[1][0], str(dict(addr=address, res=0, log="Failed to send liability specs."))
            )
            self.status = 0
            self.pending_address = None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    ev3: EV3 = EV3()

    mqtt_subscriber_thread = Thread(target=EV3.subscribe_mqtt)
    mqtt_subscriber_thread.start()

    liability_subscriber_thread = Thread(target=EV3.subscribe_new_liability)
    liability_subscriber_thread.start()
