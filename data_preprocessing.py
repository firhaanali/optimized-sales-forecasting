# -*- coding: utf-8 -*-
"""Data_Preprocessing.ipynb

Automatically generated by Colab.

Original file is located at
    https://colab.research.google.com/drive/1HgO91U0EakgohpZqfbPTkErSWr7cNLm-
"""

!pip install scikit-optimize
!pip install requests
!pip install optuna

"""# Import Library"""

# Import libraries
import warnings
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
import requests
import xgboost as xgb
import time
from sklearn.model_selection import train_test_split, TimeSeriesSplit
from sklearn.metrics import mean_squared_error, mean_absolute_error, r2_score
from sklearn.feature_selection import SelectFromModel
from skopt import BayesSearchCV
from skopt.space import Real, Integer

# Suppress warnings
warnings.filterwarnings('ignore')

# Mount Google Drive and load dataset
from google.colab import drive
drive.mount('/content/drive')

file_path = '/content/drive/My Drive/Dataset/dataset_merged.xlsx'
df = pd.read_excel(file_path)
print(f"Dataset loaded with {df.shape[0]} rows and {df.shape[1]} columns.")

"""# Data Cleaning

"""

# Standarisasi nama kolom
original_columns = df.columns.tolist()
df.columns = df.columns.str.strip().str.replace(" ", "_", regex=False)

# Check missing values
missing_values = df.isnull().sum()
missing_values = missing_values[missing_values > 0]

# Data Cleaning
df['Size'] = df['Size'].fillna('All_Size').astype(str).str.replace("Ld ", "", case=False).str.strip()
df['Payment_platform_discount'] = pd.to_numeric(df['Payment_platform_discount'], errors='coerce').fillna(0)
df['Handling_Fee'] = pd.to_numeric(df['Handling_Fee'], errors='coerce').fillna(0)

"""# Feature Engineering

Time Extraction
"""

original_columns = df.columns.tolist()

# Feature Engineering - Time Features
if 'Created_Time' in df.columns:
    df['Created_Time'] = pd.to_datetime(df['Created_Time'], errors='coerce', dayfirst=True)
    df.dropna(subset=['Created_Time'], inplace=True)

# Extract basic time features
df['year'] = df['Created_Time'].dt.year
df['month'] = df['Created_Time'].dt.month
df['day'] = df['Created_Time'].dt.day
df['hour'] = df['Created_Time'].dt.hour
df['minute'] = df['Created_Time'].dt.minute
df['second'] = df['Created_Time'].dt.second
df['day_of_week'] = df['Created_Time'].dt.dayofweek
df['day_of_year'] = df['Created_Time'].dt.dayofyear
df['quarter'] = df['Created_Time'].dt.quarter
df['week_of_year'] = df['Created_Time'].dt.isocalendar().week

# Cyclical transformations
df['month_sin'] = np.sin(2 * np.pi * df['month'] / 12)
df['month_cos'] = np.cos(2 * np.pi * df['month'] / 12)
df['day_of_week_sin'] = np.sin(2 * np.pi * df['day_of_week'] / 7)
df['day_of_week_cos'] = np.cos(2 * np.pi * df['day_of_week'] / 7)
df['day_of_year_sin'] = np.sin(2 * np.pi * df['day_of_year'] / 365)
df['day_of_year_cos'] = np.cos(2 * np.pi * df['day_of_year'] / 365)
df['hour_sin'] = np.sin(2 * np.pi * df['hour'] / 24)
df['hour_cos'] = np.cos(2 * np.pi * df['hour'] / 24)

# Calendar/event features
df['is_payday'] = df['day'].isin([25, 26, 27, 28, 29, 30, 31, 1, 2]).astype(int)
df['date'] = df['Created_Time'].dt.date
df['year_month'] = df['Created_Time'].dt.strftime('%Y-%m')
df['year_week'] = df['year'].astype(str) + '-' + df['week_of_year'].astype(str).str.zfill(2)

# Flash sale features
flash_dates = [f"{str(i).zfill(2)}-{str(i).zfill(2)}" for i in range(1, 13)]
df['flash_date_str'] = df['month'].astype(str).str.zfill(2) + '-' + df['day'].astype(str).str.zfill(2)
df['is_flash_sale'] = df['flash_date_str'].isin(flash_dates).astype(int)

# National holidays
years = [2022, 2023, 2024, 2025]
holiday_dates = []

for year in years:
    url = f"https://date.nager.at/api/v3/PublicHolidays/{year}/ID"
    response = requests.get(url)
    if response.status_code == 200:
        data = response.json()
        for holiday in data:
            holiday_dates.append(holiday['date'])

libur_nasional = pd.to_datetime(holiday_dates)
df['is_holiday'] = df['Created_Time'].dt.date.isin(libur_nasional.date).astype(int)

# Additional flags
df['is_weekend'] = (df['day_of_week'] >= 5).astype(int)
df['is_business_hour'] = (((df['hour'] >= 8) & (df['hour'] <= 17)) & (df['day_of_week'] < 5)).astype(int)
df['is_month_start'] = (df['day'] <= 7).astype(int)
df['is_month_end'] = (df['day'] >= 24).astype(int)

after_time_columns = df.columns.tolist()
added_time_features = [col for col in after_time_columns if col not in original_columns]

"""Time - Series"""

# Feature Engineering - Time Series Features
df = df.sort_values('Created_Time')
df['date'] = df['Created_Time'].dt.date

if 'Product_Name' in df.columns and 'Created_Time' in df.columns:
    # Daily sales aggregation per product
    daily_sales = df.groupby(['Product_Name', 'date'])['Quantity'].sum().reset_index()
    daily_sales = daily_sales.sort_values(['Product_Name', 'date'])

    # Create lag features
    for lag in [1, 7, 14, 30]:
        daily_sales[f'lag_{lag}_days'] = daily_sales.groupby('Product_Name')['Quantity'].shift(lag)

    # Moving Averages & Rolling Sum
    for window in [7, 14, 30]:
        daily_sales[f'moving_avg_{window}'] = daily_sales.groupby('Product_Name')['Quantity'].transform(
            lambda x: x.rolling(window=window, min_periods=1).mean()
        )
        daily_sales[f'rolling_sum_{window}'] = daily_sales.groupby('Product_Name')['Quantity'].transform(
            lambda x: x.rolling(window=window, min_periods=1).sum()
        )

    # Merge time series features to main dataframe
    df = pd.merge(
        df,
        daily_sales[['Product_Name', 'date'] +
                    [f'lag_{lag}_days' for lag in [1, 7, 14, 30]] +
                    [f'moving_avg_{window}' for window in [7, 14, 30]] +
                    [f'rolling_sum_{window}' for window in [7, 14, 30]]],
        on=['Product_Name', 'date'],
        how='left'
    )

    # Volatility features
    df['demand_std_7days'] = daily_sales.groupby('Product_Name')['Quantity'].transform(
        lambda x: x.rolling(window=7, min_periods=1).std()
    )

    # Sales trends and ratios
    df['sales_trend'] = (df['lag_7_days'] - df['lag_14_days']) / (df['lag_14_days'] + 1) * 100
    df['sales_ratio_to_avg'] = df['Quantity'] / (df['moving_avg_30'] + 1)

# Flash sale features
df['flash_sale_yesterday'] = df.groupby('Product_Name')['is_flash_sale'].shift(1).fillna(0)
df['days_since_last_flash'] = (
    df[::-1].groupby('Product_Name')['is_flash_sale']
    .apply(lambda x: x.cumsum().shift(-1).fillna(0))
    .reset_index(level=0, drop=True)[::-1]
)

# Holiday and payday features
df['holiday_yesterday'] = df['is_holiday'].shift(1).fillna(0)
df['payday_yesterday'] = df['is_payday'].shift(1).fillna(0)

# Additional time features
df['week_of_month'] = pd.to_datetime(df['date']).dt.day.apply(lambda d: (d - 1) // 7 + 1)

# Monthly seasonal index
if 'month' in df.columns:
    # Calculate monthly average per product
    monthly_avg = df.groupby(['Product_Name', 'month'])['Quantity'].mean().reset_index()

    # Calculate overall average per product
    product_avg = monthly_avg.groupby('Product_Name')['Quantity'].mean().reset_index()

    # Join to calculate seasonal index
    monthly_avg = pd.merge(monthly_avg, product_avg, on='Product_Name', suffixes=('_month', '_product'))
    monthly_avg['seasonal_index'] = monthly_avg['Quantity_month'] / monthly_avg['Quantity_product']

    # Join seasonal index to main dataframe
    df = pd.merge(df, monthly_avg[['Product_Name', 'month', 'seasonal_index']],
                  on=['Product_Name', 'month'], how='left')

# Fill NaN values for all time series features
ts_columns = [col for col in df.columns if 'lag_' in col or 'moving_avg_' in col or
              'trend' in col or 'ratio' in col or 'seasonal' in col or
              'std' in col or 'flash' in col or 'holiday_yesterday' in col or
              'payday_yesterday' in col]
for col in ts_columns:
    if col in df.columns:
        df[col] = df[col].fillna(0)

# Drop and Save
created_times = df['Created_Time'].copy()
product_names = df['Product_Name'].copy()
variations = df['Variation'].copy() if 'Variation' in df.columns else None
sizes = df['Size'].copy() if 'Size' in df.columns else None

# Drop non-feature columns
df.drop(columns=['Created_Time', 'date', 'year_month', 'year_week'], inplace=True, errors='ignore')
df.drop(columns=['Product_Name', 'Variation', 'Size'], inplace=True, errors='ignore')

after_ts_columns = df.columns.tolist()
added_ts_features = [col for col in after_ts_columns if col not in after_time_columns]

"""# Data Split"""

# Train-Validation-Test Split
target_column = 'Quantity'
columns_to_drop = ['year_month', 'date', 'flash_date_str']

# Separate features and target
X = df.drop(columns=[target_column] + [col for col in columns_to_drop if col in df.columns])
y = np.log1p(df[target_column])  # Log transform for better model performance

# Time-based split
train_size = int(0.8 * len(df))
valid_size = int(0.1 * len(df))

X_train = X.iloc[:train_size]
y_train = y.iloc[:train_size]
X_valid = X.iloc[train_size:train_size+valid_size]
y_valid = y.iloc[train_size:train_size+valid_size]
X_test = X.iloc[train_size+valid_size:]
y_test = y.iloc[train_size+valid_size:]

# Hitung jumlah sampel
counts = [len(X_train), len(X_valid), len(X_test)]
labels = ['Training', 'Validation', 'Testing']
percentages = [count / (len(X_train) + len(X_valid) + len(X_test)) * 100 for count in counts]

# Feature Selection
feature_selector = xgb.XGBRegressor(random_state=42)
feature_selector.fit(X_train, y_train)

feature_importance = pd.DataFrame({
    'Feature': X_train.columns,
    'Importance': feature_selector.feature_importances_
})
feature_importance = feature_importance.sort_values(by='Importance', ascending=False)

importance_mean = feature_importance['Importance'].mean()
print(f"Mean Importance Score: {importance_mean:.4f}")

selector = SelectFromModel(feature_selector, threshold=importance_mean, prefit=True)
selected_features = X_train.columns[selector.get_support()]
print(f"Selected {len(selected_features)} features from total {X_train.shape[1]} initial features.")

# Use selected features for all data subsets
X_train = X_train[selected_features]
X_valid = X_valid[selected_features]
X_test = X_test[selected_features]

# Filter hanya fitur yang dipilih
selected_feature_importance = feature_importance[feature_importance['Feature'].isin(selected_features)]