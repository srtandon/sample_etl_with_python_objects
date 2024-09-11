import sys
import os
import yaml
import logging
import random

# Set up logging
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

class TrialBase:
    """A base class for all clinical trial-related classes."""
    def __init__(self, name, import_config: dict, *args, **kwargs):
        self.trial_name = name
        self.trial_config = import_config.get(name, {})
        super().__init__(*args, **kwargs)
        self.load_data()

    def load_data(self):
        """Load trial data from the file specified in the configuration."""
        path = os.getcwd()
        data_path = self.trial_config.get('data_file', 'sample_trial_data.yaml')
        data_file = path + data_path
        if os.path.exists(data_file):
            with open(data_file, 'r') as f:
                all_trial_data = yaml.safe_load(f)
            self.trial_data = all_trial_data.get(self.trial_name, {})
            if self.trial_data:
                print(self.trial_data)
                logger.info(f"Loaded data for {self.trial_name} from {data_file}")
            else:
                logger.warning(f"No data found for {self.trial_name} in {data_file}")
        else:
            logger.warning(f"Data file {data_file} not found. Continuing without data.")
            self.trial_data = {}
    
    def display_info(self):
        """Display trial name and config."""
        print(f"Trial Name: {self.trial_name}")
        for k, v in self.trial_config.items():
            print(f"Config {k}: {v}")

class TrialA(TrialBase):
    """Clinical Trial A."""
    def __init__(self, name, import_config, *args, **kwargs):
        self.name = name
        self.import_config = import_config
        super().__init__(name, import_config, *args, **kwargs)
        
    def some_trial_a_function(self):
        print(f"My name is {self.trial_name}")
        self.display_info()

class TrialB(TrialBase):
    """Clinical Trial B with predefined cohorts and patient counts."""
    def __init__(self, name, import_config, *args, **kwargs):
        super().__init__(name, import_config, *args, **kwargs)
        self.some_trial_b_function()

    def some_trial_b_function(self):
        print(f"Hello! My name is {self.trial_name.upper()}.")
        print(f'My keys are: {list(self.trial_config.keys())}')
    

class TrialC(TrialBase):
    """Clinical Trial C."""
    def __init__(self, name, config, ongoing: bool):
        super().__init__(name, config)
        self.ongoing = ongoing

class Cohort:
    """Handles cohort-related logic."""
    def __init__(self, trial_class, *args, **kwargs):
        self.trial_instance = trial_class(*args, **kwargs)
        self.cohort_config = self.trial_instance.trial_config.get('cohorts', [])
        self.cohort_data = self.trial_instance.trial_data.get('cohorts', {})
        self.dose_data = self.process_data()
        # for cohort in self.cohort_config:
        #     logger.info(f"Cohort: {cohort}")
        #     self.print_patient_count(cohort)

    def process_data(self):
        """Process cohort-specific data based on configuration."""
        processed_data = {}
        for cohort in self.cohort_config:
            cohort_info = self.cohort_data.get(cohort,{})
            processed_data[cohort] = len(cohort_info.get('d',[]))*cohort_info.get('dose',0)
        logger.info(f"Processed data for cohorts: {processed_data}")
        return processed_data
    
    def print_patient_count(self, cohort_name):
        """Print the number of patients for a specific cohort."""
        patient_count = self.cohort_data.get(cohort_name,{}).get('patient_count', 0)
        print(f"Cohort {cohort_name}: {patient_count} patients enrolled.")

class Schedule:
    """Schedule class that handles scheduling for a clinical trial."""
    def __init__(self, trial_class, *args, **kwargs):
        self.trial_instance = trial_class(*args, **kwargs)
        self.cohorts = self.trial_instance.trial_config.get('cohorts', [])
        self.collect_schedule = self.trial_instance.trial_config.get('schedule', False)
        if self.check_conditions():
            logger.info(f"Commencing data fetch cohorts: {[c[0] for c in self.cohorts]}")
            if self.collect_schedule:
                self.fetch_schedule()
        else:
            m = 'No cohorts were passed. Import pipeline exiting...'
            logger.error(m)
            self.pipeline_di = {'failure': True, 'critical': True, 'message': m}

    def check_conditions(self):
        """Check for cohort conditions."""
        if self.cohorts == 'all':
            logger.info('All cohorts selected.')
            return True
        elif self.cohorts:
            logger.info(f"Selected cohort(s): {', '.join([str(cohort) for cohort in self.cohorts])}")
            return True
        else:
            logger.info('No cohorts selected.')
            return False

    def fetch_schedule(self):
        """Process the schedule for the trial."""
        logger.info('Collecting schedule for the trial.')
        schedule_data = self.trial_instance.trial_data.get('schedule', {})
        print(f'Schedule data: {schedule_data}')
        logger.info(f'Schedule data: {schedule_data}')

def create_import(endpoint_class):
    """Create a trial import job for any endpoint class."""
    class TrialImportJob(endpoint_class):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.pipeline_di = {'failure': False}

        def introduce(self):
            """Introduce the trial."""
            if hasattr(self, 'trial_instance') and hasattr(self.trial_instance, 'trial_name'):
                logger.info(f"Inherited {self.trial_instance.trial_name} and associated values.")
                print(f"Welcome to the {self.trial_instance.trial_name} clinical trial!")
            else:
                self.pipeline_di = {'failure': True, 'critical': True}
                logger.error('Failed to inherit expected class. Check function arguments.')

        def run_import(self):
            """Run the import process."""
            self.introduce()
            self.check_conditions()
            if self.collect_schedule:
                self.fetch_schedule()


    return TrialImportJob


if __name__ == "__main__":
    # Load config
    path_lead = os.getcwd()
    print(path_lead)
    with open(path_lead + '/playground/sample_ingestion_config.yaml') as y:
        trial_dict = yaml.safe_load(y)
    
    # Create and run the import job
    schedule_import = create_import(Schedule)(
        trial_class=TrialB,
        name="TrialB", 
        import_config=trial_dict
    )
    schedule_import.run_import()
    
    # Create and run the import job for Cohort
    cohort_import = create_import(Cohort)(
        trial_class=TrialB,
        name="TrialB", 
        import_config=trial_dict
    )
    if cohort_import.cohort_config:
        random_cohort = random.choice(cohort_import.cohort_config)
        cohort_import.print_patient_count(random_cohort)
    else:
        print("No cohorts available for processing.")

