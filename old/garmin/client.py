from garminconnect import Garmin

class GarminClient:
    def __init__(self, email, password):
        self.client = Garmin(email, password)
        self.client.login()

    def get_health_data(self, startdate, enddate):
        return self.client.get_stats(startdate.isoformat())

    def get_activities(self, start, limit):
        return self.client.get_activities(start, limit)
