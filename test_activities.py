import json
from datetime import datetime, timedelta
from garminconnect import Garmin

class GarminClient:
    def __init__(self, email, password):
        self.client = Garmin(email, password)
        self.client.login()

    def get_activities(self, start, limit):
        """Return available activities."""
        activities = self.client.get_activities(start, limit)
        return activities

def collect_activities(email, password, months=18):
    garmin_client = GarminClient(email, password)
    
    today = datetime.today().date()
    date_n_months_ago = today - timedelta(days=months*30)

    new_data = []
    start = 0
    limit = 50  # Number of activities to fetch per request

    while True:
        print(f"Fetching activities from {start} to {start + limit}")
        try:
            activities = garmin_client.get_activities(start, limit)
            if not activities:
                break
            for activity in activities:
                activity_date_time = activity['startTimeLocal']
                activity_date_obj = datetime.strptime(activity_date_time, '%Y-%m-%d %H:%M:%S').date()
                if activity_date_obj >= date_n_months_ago and activity_date_obj <= today:
                    new_data.append(activity)
            if len(activities) < limit:
                break
            start += limit
        except Exception as e:
            print(f"Error fetching activities: {e}")
            break

    if new_data:
        with open('activities_data.json', 'w') as f:
            print("Saving data to activities_data.json")
            json.dump(new_data, f)
        print(f"Collected {len(new_data)} activities.")
    else:
        print("No new activities to save")
        
if __name__ == '__main__':
    email = 'duwat.adrien@gmail.com'
    password = 'Duwat9897.'
    collect_activities(email, password, months=18)
