import copy
import pickle

from confluent_kafka import Consumer, Producer

from ..run_engine import Dispatcher, DocumentNames


def delivery_report(err, msg):
    """ Called once for each message produced to indicate delivery result.
        Triggered by poll() or flush(). """
    if err is not None:
        print('Message delivery failed: {}'.format(err))
    else:
        print('Message delivered to {} [{}]'.format(msg.topic(),
                                                    msg.partition()))


class Publisher:
    """
    A callback that publishes documents to a Kafka server.

    Reference: https://github.com/confluentinc/confluent-kafka-python/issues/137

    Parameters
    ----------
    address : string
        Address of a running Kafka server as a string like
        ``'127.0.0.1:9092'``
    serializer: function, optional
        optional function to serialize data. Default is pickle.dumps.

    Example
    -------

    Publish from a RunEngine to a Kafka server on localhost on port 9092.

    >>> publisher = Publisher('localhost:9092')
    >>> RE = RunEngine({})
    >>> RE.subscribe(publisher)
    """
    def __init__(self, address, *,
                 serializer=pickle.dumps):
        self.address = address
        self.producer = Producer({'bootstrap.servers': self.address})
        self._serializer = serializer

    def __call__(self, name, doc):
        doc = copy.deepcopy(doc)
        try:
            self.producer.produce('bluesky-event',
                                  self._serializer((name, doc)),
                                  callback=delivery_report)
            self.producer.poll(0)
        except BufferError as be:
            # poll(...) blocks until there is space on the queue
            self.producer.poll(10)
            # repeat produce(...) now that some time has passed
            self.producer.produce(topic='bluesky-event',
                                  value=doc,
                                  callback=self.delivery_report)

    def close(self):
        self.producer.flush()


class RemoteDispatcher(Dispatcher):
    """
    Dispatch documents received over the network from a Kafka server.

    Parameters
    ----------
    address : str or tuple
        Address of a Kafka server as a string like ``'127.0.0.1:9092'``
    deserializer: function, optional
        optional function to deserialize data. Default is pickle.loads.

    Example
    -------

    Print all documents generated by remote RunEngines.

    >>> d = RemoteDispatcher('localhost:9092')
    >>> d.subscribe(print)
    >>> d.start()  # runs until interrupted
    """
    def __init__(self, address, *,
                 group_id='kafka-bluesky',
                 deserializer=pickle.loads):
        self.address = address
        self._deserializer = deserializer

        consumer_params = {
            'bootstrap.servers': self.address,
            'group.id': group_id,
            'auto.offset.reset': 'latest'
        }
        self.consumer = Consumer(consumer_params)
        self.consumer.subscribe(topics=['bluesky-event'])
        self.closed = False

        super().__init__()

    def _poll(self):
        while True:
            msg = self.consumer.poll(1.0)

            if msg is None:
                # no message was found
                pass
            elif msg.error():
                print('Consumer error: {}'.format(msg.error()))
            else:
                print('msg is "{}"'.format(msg.topic()))
                name, doc = self._deserializer(msg.value())
                print(f'"{name}":\n{doc}')
                self.process(DocumentNames[name], doc)

    def start(self):
        if self.closed:
            raise RuntimeError("This RemoteDispatcher has already been "
                               "started and interrupted. Create a fresh "
                               "instance with {}".format(repr(self)))
        try:
            self._poll()
        except:
            self.stop()
            raise

    def stop(self):
        self.consumer.close()
        self.closed = True